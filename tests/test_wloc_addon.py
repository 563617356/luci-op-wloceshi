"""wloc_addon 集成测试（不需要真实 mitmproxy）。

用假的 flow 对象驱动 WlocAddon.response()，验证：
  * 只命中 WLOC 端点才改包
  * gzip / 明文两种回包都能处理
  * 目标为空时透传
  * 改包失败时放行且不抛异常

运行：python3 tests/test_wloc_addon.py
"""

import gzip
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "root", "usr", "libexec", "wloc"))

import wloc_addon  # noqa: E402
from wloc_proto import encode_field  # noqa: E402

FAILURES = []
PASSED = 0


def check(condition, label):
    global PASSED
    if condition:
        PASSED += 1
        print("  ok   %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL %s" % label)


# ---------------------------------------------------------------- 假对象

class FakeRequest(object):
    def __init__(self, host, path):
        self.pretty_host = host
        self.path = path


class FakeResponse(object):
    def __init__(self, content):
        self.content = content
        self.headers = {}


class FakeFlow(object):
    def __init__(self, host, path, content):
        self.request = FakeRequest(host, path)
        self.response = FakeResponse(content)


def make_wloc_body(lat=22.544577, lon=113.94114, acc=25):
    def position(la, lo, a):
        return (
            encode_field(1, 0, round(1e8 * la))
            + encode_field(2, 0, round(1e8 * lo))
            + encode_field(3, 0, a)
        )

    mac = b"aa:bb:cc:dd:ee:ff"
    wifi = encode_field(1, 2, mac) + encode_field(2, 2, position(lat, lon, acc))
    cell = encode_field(5, 2, position(lat, lon, acc))
    payload = encode_field(2, 2, wifi) + encode_field(22, 2, cell)
    return b"\xff" * 16 + struct.pack(">H", len(payload)) + payload + encode_field(11, 0, 3)


def make_addon(lat, lon, acc=25, enabled=True):
    addon = wloc_addon.WlocAddon()
    addon.config.enabled = enabled
    addon.config.latitude = lat
    addon.config.longitude = lon
    addon.config.accuracy = acc
    addon.config.log_level = "off"
    return addon


def find_coords(buf):
    """从改写结果中抠出第一个 (lat, lon)。"""
    from wloc_proto import WlocParseError, parse_fields

    def walk(data, depth=0):
        if depth > 5:
            return None
        try:
            fields = parse_fields(data)
        except WlocParseError:
            return None
        has1 = any(f[0] == 1 and f[1] == 0 for f in fields)
        has2 = any(f[0] == 2 and f[1] == 0 for f in fields)
        if has1 and has2:
            la = next(f[2] for f in fields if f[0] == 1 and f[1] == 0)
            lo = next(f[2] for f in fields if f[0] == 2 and f[1] == 0)
            return la / 1e8, lo / 1e8
        for _fno, ftype, value, _raw in fields:
            if ftype == 2:
                got = walk(value, depth + 1)
                if got:
                    return got
        return None

    frame_len = struct.unpack(">H", buf[16:18])[0]
    return walk(buf[18:18 + frame_len])


# ---------------------------------------------------------------- 测试

def test_plain_body():
    print("plain body")
    body = make_wloc_body()
    addon = make_addon(39.9042, 116.4074, 30)
    flow = FakeFlow("gs-loc.apple.com", "/clls/wloc?cls=foo", body)

    addon.response(flow)

    check(addon.stats.requests == 1, "request counted")
    check(addon.stats.patched == 1, "response patched")
    check(addon.stats.locations == 2, "two positions rewritten (%d)" % addon.stats.locations)
    got = find_coords(flow.response.content)
    check(
        got is not None and abs(got[0] - 39.9042) < 1e-6 and abs(got[1] - 116.4074) < 1e-6,
        "coordinates rewritten to target (%s)" % (got,),
    )


def test_gzip_body():
    print("gzip body")
    body = gzip.compress(make_wloc_body())
    addon = make_addon(31.2304, 121.4737, 15)
    flow = FakeFlow("gs-loc-cn.apple.com", "/clls/wloc", body)

    addon.response(flow)

    check(addon.stats.patched == 1, "gzip response patched")
    got = find_coords(flow.response.content)
    check(
        got is not None and abs(got[0] - 31.2304) < 1e-6 and abs(got[1] - 121.4737) < 1e-6,
        "gzip coordinates rewritten (%s)" % (got,),
    )


def test_wrong_host_untouched():
    print("non-target host untouched")
    original = make_wloc_body()
    addon = make_addon(39.9042, 116.4074, 30)

    flow = FakeFlow("www.apple.com", "/clls/wloc", original)
    addon.response(flow)

    check(addon.stats.requests == 0, "request not counted")
    check(addon.stats.patched == 0, "nothing patched")
    check(flow.response.content == original, "body byte-identical")

    flow2 = FakeFlow("gs-loc.apple.com", "/some/other/path", original)
    addon.response(flow2)
    check(addon.stats.patched == 0, "other path on target host untouched")
    check(flow2.response.content == original, "body byte-identical for other path")


def test_passthrough():
    print("passthrough modes")
    original = make_wloc_body()

    addon = make_addon(None, None)
    flow = FakeFlow("gs-loc.apple.com", "/clls/wloc", original)
    addon.response(flow)
    check(flow.response.content == original, "empty target -> passthrough")
    check(addon.stats.passthrough == 1, "passthrough counted")

    addon2 = make_addon(39.9042, 116.4074, 30, enabled=False)
    flow2 = FakeFlow("gs-loc.apple.com", "/clls/wloc", original)
    addon2.response(flow2)
    check(flow2.response.content == original, "disabled -> passthrough")

    addon3 = make_addon(22.544577, 113.94114, 25)
    flow3 = FakeFlow("gs-loc.apple.com", "/clls/wloc", original)
    addon3.response(flow3)
    check(flow3.response.content == original, "default coordinates -> passthrough")


def test_garbage_survives():
    print("malformed input survives")
    addon = make_addon(39.9042, 116.4074, 30)

    garbage = bytes(range(256))
    flow = FakeFlow("gs-loc.apple.com", "/clls/wloc", garbage)
    addon.response(flow)
    check(flow.response.content == garbage, "garbage body passed through unchanged")
    check(addon.stats.errors >= 1, "error counted")

    empty = FakeFlow("gs-loc.apple.com", "/clls/wloc", b"")
    addon.response(empty)
    check(addon.stats.patched == 0, "empty body counts as passthrough, not patch")
    check(addon.stats.passthrough == 1, "empty body counted as passthrough")

    bad_gzip = FakeFlow("gs-loc.apple.com", "/clls/wloc", b"\x1f\x8b" + b"\x00" * 20)
    addon.response(bad_gzip)
    check(bad_gzip.response.content == b"\x1f\x8b" + b"\x00" * 20,
          "broken gzip passed through unchanged")


def test_out_of_range():
    print("out-of-range coordinates rejected")
    original = make_wloc_body()
    for lat, lon, label in (
        (91.0, 116.0, "latitude > 90"),
        (-91.0, 116.0, "latitude < -90"),
        (39.0, 181.0, "longitude > 180"),
        (39.0, -181.0, "longitude < -180"),
    ):
        addon = make_addon(lat, lon, 30)
        flow = FakeFlow("gs-loc.apple.com", "/clls/wloc", original)
        addon.response(flow)
        check(flow.response.content == original, "%s -> passthrough" % label)


def main():
    print("=" * 62)
    print("wloc_addon integration tests")
    print("=" * 62)
    for fn in (
        test_plain_body,
        test_gzip_body,
        test_wrong_host_untouched,
        test_passthrough,
        test_garbage_survives,
        test_out_of_range,
    ):
        fn()
        print("")

    print("=" * 62)
    if FAILURES:
        print("FAILED %d / %d" % (len(FAILURES), PASSED + len(FAILURES)))
        for name in FAILURES:
            print("  - %s" % name)
        return 1
    print("ALL %d CHECKS PASSED" % PASSED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
