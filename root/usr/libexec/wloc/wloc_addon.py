"""wloc mitmproxy addon —— 在路由器上改写 Apple WLOC 网络定位坐标。

设计要点：
  * 只对 gs-loc.apple.com / gs-loc-cn.apple.com 做 MITM，
    其余流量靠 --ignore-hosts 走裸隧道，不解密、不落盘。
  * 配置从 /etc/config/wloc 经 uci 读入，改配置后自动热重载，
    无需重启 mitmproxy。
  * 任何异常都放行原始响应——定位服务返回畸形数据比不改更糟。

命令行参数见 wloc-ctl 或 init 脚本。
"""

import gzip
import json
import os
import subprocess
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wloc_proto import NoPatchNeeded, WlocParseError, is_gzip, patch_wloc_body  # noqa: E402

try:
    from mitmproxy import ctx, http
except ImportError:  # pragma: no cover - 允许在无 mitmproxy 环境下做语法检查
    ctx = None
    http = None

TARGET_HOSTS = frozenset(("gs-loc.apple.com", "gs-loc-cn.apple.com"))
WLOC_PATH_MARKER = "/clls/wloc"

UCI_CONFIG = "wloc"
STATE_DIR = "/var/run/wloc"
STATE_FILE = os.path.join(STATE_DIR, "stats.json")

DEFAULT_LONGITUDE = 113.94114
DEFAULT_LATITUDE = 22.544577
DEFAULT_ACCURACY = 25

LEVELS = {
    "off": 0,
    "error": 1,
    "warn": 2,
    "info": 3,
    "debug": 4,
    "all": 5,
}


def _log(level, message):
    """写日志。ctx.log 在 mitmproxy 启动前不存在，必须惰性取用。"""
    if ctx is None:
        return
    log = getattr(ctx, "log", None)
    if log is None:
        return
    text = "[wloc] " + message
    if level == "error":
        log.error(text)
    elif level == "warn":
        log.warn(text)
    elif level == "debug":
        log.debug(text)
    else:
        log.info(text)


def _uci(*args):
    """读 UCI 配置。失败返回 None，不抛异常。"""
    try:
        proc = subprocess.Popen(
            ["uci", "-q", "get"] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, _ = proc.communicate()
        if proc.returncode != 0:
            return None
        return out.decode("utf-8", "replace").strip()
    except (OSError, ValueError):
        return None


class Config(object):
    """运行配置，带 mtime 缓存实现热重载。"""

    def __init__(self):
        self._stamp = None
        self.enabled = False
        self.latitude = None
        self.longitude = None
        self.accuracy = DEFAULT_ACCURACY
        self.log_level = "info"
        self.reload(force=True)

    def _config_mtime(self):
        for path in ("/etc/config/wloc",):
            try:
                return os.path.getmtime(path)
            except OSError:
                return 0.0
        return 0.0

    def reload(self, force=False):
        stamp = self._config_mtime()
        if not force and stamp == self._stamp:
            return False
        self._stamp = stamp

        enabled = (_uci(UCI_CONFIG, "main", "enabled") or "0").strip()
        self.enabled = enabled in ("1", "on", "true", "yes", "enabled")

        lon = _uci(UCI_CONFIG, "main", "longitude")
        lat = _uci(UCI_CONFIG, "main", "latitude")
        acc = _uci(UCI_CONFIG, "main", "accuracy")
        level = _uci(UCI_CONFIG, "main", "log_level")

        self.longitude = _to_float(lon)
        self.latitude = _to_float(lat)
        self.accuracy = _to_int(acc, DEFAULT_ACCURACY)
        self.log_level = (level or "info").strip().lower()
        if self.log_level not in LEVELS:
            self.log_level = "info"
        return True

    def target(self):
        """返回 (lat, lon, acc)，无效时返回 None 表示透传。"""
        if not self.enabled:
            return None
        if self.latitude is None or self.longitude is None:
            return None
        # 保持与原项目一致的默认坐标透传语义
        if (self.longitude == DEFAULT_LONGITUDE
                and self.latitude == DEFAULT_LATITUDE):
            return None
        if not (-90.0 <= self.latitude <= 90.0):
            return None
        if not (-180.0 <= self.longitude <= 180.0):
            return None
        if self.accuracy <= 0:
            return None
        return self.latitude, self.longitude, self.accuracy

    def level_enabled(self, level):
        return LEVELS.get(self.log_level, 3) >= LEVELS.get(level, 3)


def _to_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value, fallback):
    if value is None or value == "":
        return fallback
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


class Stats(object):
    """运行统计，写到 /var/run/wloc/stats.json 供 LuCI 读取。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.started = time.time()
        self.requests = 0
        self.patched = 0
        self.passthrough = 0
        self.errors = 0
        self.locations = 0
        self.wifi = 0
        self.cell = 0
        self.last_time = 0
        self.last_message = ""

    def as_dict(self):
        return {
            "uptime": int(time.time() - self.started),
            "requests": self.requests,
            "patched": self.patched,
            "passthrough": self.passthrough,
            "errors": self.errors,
            "locations": self.locations,
            "wifi": self.wifi,
            "cell": self.cell,
            "last_time": self.last_time,
            "last_message": self.last_message,
        }

    def flush(self, config=None):
        if not _LEVEL_ALLOW_DISK:
            return
        try:
            if not os.path.isdir(STATE_DIR):
                os.makedirs(STATE_DIR)
            payload = self.as_dict()
            if config is not None:
                target = config.target()
                payload["enabled"] = config.enabled
                payload["passthrough_mode"] = target is None
                payload["log_level"] = config.log_level
                if target is not None:
                    payload["latitude"], payload["longitude"], payload["accuracy"] = target
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w") as handle:
                json.dump(payload, handle)
            os.rename(tmp, STATE_FILE)
        except (OSError, IOError):
            pass


_LEVEL_ALLOW_DISK = os.access("/var/run", os.W_OK)


class WlocAddon(object):
    """mitmproxy addon 主体。"""

    def __init__(self):
        self.config = Config()
        self.stats = Stats()
        self._last_reload = 0.0
        self._last_flush = 0.0

    # ------------------------------------------------------------ mitmproxy

    def load(self, loader):
        _log("info", "loaded, waiting for traffic")

    def running(self):
        target = self.config.target()
        if target is None:
            _log("info", "passthrough mode active (no target coordinates set)")
        else:
            _log(
                "info",
                "active target lat=%s lon=%s acc=%s"
                % (target[0], target[1], target[2]),
            )
        self.stats.flush(self.config)

    def done(self):
        self.stats.flush(self.config)

    def response(self, flow):
        """响应钩子：命中 WLOC 端点就改写坐标。"""
        now = time.time()
        if now - self._last_reload > 2.0:
            self._last_reload = now
            if self.config.reload():
                _log("info", "configuration reloaded")

        if not _is_wloc_flow(flow):
            return

        self.stats.requests += 1
        target = self.config.target()

        if target is None:
            self.stats.passthrough += 1
            _log("debug", "passthrough (no target set)")
            self._maybe_flush()
            return

        try:
            self._handle(flow, target)
        except Exception as exc:  # noqa: BLE001 - 绝不因改包失败影响定位
            self.stats.errors += 1
            self.stats.last_message = str(exc)
            _log("error", "patch failed, passing through: %s" % exc)
        finally:
            self._maybe_flush()

    # ------------------------------------------------------------ 内部

    def _handle(self, flow, target):
        lat, lon, acc = target
        original = flow.response.content
        if not original:
            self.stats.passthrough += 1
            return

        body = original
        was_gzip = False
        if is_gzip(body):
            try:
                body = gzip.decompress(body)
                was_gzip = True
            except (OSError, EOFError, zlib.error):
                _log("debug", "gzip body present but undecompressable")
                self.stats.errors += 1
                return

        try:
            patched, stats = patch_wloc_body(body, lat, lon, acc)
        except NoPatchNeeded:
            self.stats.passthrough += 1
            self.stats.last_message = "target already matches"
            _log("debug", "target coordinates already match, no change")
            return
        except WlocParseError as exc:
            self.stats.errors += 1
            self.stats.last_message = str(exc)
            _log("warn", "no patchable payload: %s" % exc)
            return

        # flow.response.content 接受的是"解码后"的字节，
        # mitmproxy 会依据原 Content-Encoding 重新压缩并重算 Content-Length。
        # 所以这里统一交回明文，不自己处理压缩。
        flow.response.content = patched

        self.stats.patched += 1
        self.stats.locations += stats.locations
        self.stats.wifi += stats.wifi
        self.stats.cell += stats.cell
        self.stats.last_time = int(time.time())
        self.stats.last_message = "patched %d position(s)" % stats.locations
        _log(
            "info",
            "patched lat=%s lon=%s locations=%d wifi=%d cell=%d gzip=%s"
            % (lat, lon, stats.locations, stats.wifi, stats.cell, was_gzip),
        )

    def _maybe_flush(self):
        now = time.time()
        if now - self._last_flush >= 2.0:
            self._last_flush = now
            self.stats.flush(self.config)


def _is_wloc_flow(flow):
    try:
        request = flow.request
    except AttributeError:
        return False
    if request.pretty_host not in TARGET_HOSTS:
        return False
    return WLOC_PATH_MARKER in request.path


addons = [WlocAddon()]
