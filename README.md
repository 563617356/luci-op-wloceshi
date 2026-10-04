# luci-app-wloc

在 OpenWrt 路由器上改写 Apple WLOC 网络定位坐标的 LuCI 应用。

连接路由器 Wi-Fi 的 iPhone，其网络定位（Wi-Fi/基站）返回的坐标会被替换为指定位置。
所有处理都在本机完成，不依赖任何外部服务器。

移植自 [Yu9191/wloc](https://github.com/Yu9191/wloc)（MIT）的代理脚本方案——
原方案跑在 Surge / Quantumult X 等手机端代理里，本项目把 MITM 环节搬到路由器上，
使同一网络内的多台设备都能共用，且不需要在每台设备上装代理 App。

---

## 工作原理

```
iPhone (Wi-Fi 代理 → 路由器:8080)
        ↓
mitmproxy  仅对 gs-loc(-cn).apple.com 解密，其余走裸隧道
        ↓
wloc_addon.py  拦截 /clls/wloc 回包 → gunzip → 改 protobuf 坐标 → regzip
        ↓
gs-loc.apple.com
```

Apple 的网络定位服务 `gs-loc.apple.com/clls/wloc` 返回 protobuf 编码的定位数据。
本项目解析其结构，将每个定位条目的 WGS-84 坐标改写为目标值。

响应结构：

| 字段 | 含义 | 改写动作 |
|---|---|---|
| frame | `[base+8 字节头][2 字节大端长度][payload][tail]` | 长度随 payload 变化而重写 |
| payload field 2 | Wi-Fi 定位条目列表 | 改写 |
| payload field 22 / 24 | 基站定位条目列表 | 改写 |
| Wi-Fi field 1 | MAC 地址 | 仅用于识别，保留原值 |
| Wi-Fi field 2 | 定位数据 | 改写 |
| 基站 field 5 | 定位数据 | 改写 |
| 定位 field 1 | 纬度（varint 定点数 ×1e8） | 改写 |
| 定位 field 2 | 经度（varint 定点数 ×1e8） | 改写 |
| 定位 field 3 | 精度半径（米） | 改写 |

坐标系说明：Apple WLOC 使用 WGS-84。地图选点给出的是 GCJ-02（国内地图偏移坐标），
界面会自动做 GCJ-02 → WGS-84 反算，误差 < 0.1m。境外坐标自动跳过换算。

### 获取坐标的三种方式

**方式一：粘贴地图分享链接（境外推荐）**

在 iPhone 上用任意地图 App 长按选点 → 分享 → 复制链接 → 粘贴到
LuCI 界面「从地图链接导入坐标」区块 → 点「解析并填入」。

支持格式：

| 来源 | 链接特征 | 坐标系 |
|---|---|---|
| Apple 地图 | `maps.apple.com/?ll=纬度,经度` | WGS-84（境外无偏移） |
| Google 地图 | `?q=` 或 `@纬度,经度,15z` | WGS-84 |
| 高德 | `?p=..,纬度,经度` 或 `?q=` | GCJ-02（境内自动反算） |
| 腾讯 | `?ll=纬度,经度` | GCJ-02（境内自动反算） |
| 纯文本 | `纬度,经度` | 按 GCJ-02 处理 |

坐标系按**域名**判定而非参数名——高德和 Google 都用 `?q=`，只看参数名会判错。

链接解析不渲染任何地图，因此**不依赖任何地图服务，全球可用**。
这也是境外使用 Apple 地图的正确路径：Apple 地图本身是境外最佳选择，
但其瓦片不嵌入本界面，改为读取它分享出来的坐标。

**方式二：地图选点**

界面上方切换腾讯地图 / 高德地图，直接点地图选点。详见下节。

**方式三：手工填写**

直接在上方「目标纬度」「目标经度」填 WGS-84 数值。

### 地图源

选点界面支持两种底图，可随时切换：

| 地图源 | 配置 | 说明 |
|---|---|---|
| 腾讯地图（默认） | 无需任何配置 | key 由后端代理持有，前端不接触 key |
| 高德地图 | 需自备 key | 在 [lbs.amap.com](https://lbs.amap.com/api/webservice/guide/api-key) 申请「Web端(JS API)」类型 |

两者底图同属 GCJ-02 体系，切换后坐标可直接复用，无需重新选点。

切换方式：LuCI 界面「地图选点」区块顶部的按钮，或在配置文件中：

```sh
uci set wloc.main.map_provider='amap'
uci set wloc.main.amap_key='你的key'
uci commit wloc
```

> **关于 Apple 地图 / Google 地图**
> 这两者不在国内地图服务合规白名单内，因此**地图瓦片不接入**本界面。
> 但这不影响境外使用：Apple 地图与 Google 地图在境外本来就是最佳选择，
> 用「方式一：粘贴分享链接」即可取到它们给出的坐标——
> 链接解析不渲染地图，全球可用，且境外坐标本就是 WGS-84，无需任何换算。

---

## 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| RAM | **≥ 256 MB** | mitmproxy 稳态占用 100~200 MB |
| Flash | 建议外挂存储 | Python3 + mitmproxy 约 150 MB |
| 架构 | x86_64 最佳，arm64 可试 | OpenWrt 是 musl，依赖需编译 |
| OpenWrt | 21.02 及以上 | 需支持 procd |

> ⚠️ **128 MB RAM 的机型（如 MT7621）不建议使用**，mitmproxy 会耗尽内存导致 OOM。

---

## 安装

### 1. 添加 feed

在 OpenWrt 根目录的 `feeds.conf.default`（或 `feeds.conf`）追加：

```
src-link wloc /path/to/luci-app-wloc/luci
```

然后：

```sh
./scripts/feeds update wloc
./scripts/feeds install wloc
```

### 2. 编译

```sh
make package/luci-app-wloc/compile V=s
make package/luci-app-wloc-mitm/compile V=s
```

产物在 `bin/packages/<arch>/luci/` 下。

### 3. 部署到设备

```sh
scp bin/packages/<arch>/luci/luci-app-wloc_*.ipk root@192.168.1.1:/tmp/
scp bin/packages/<arch>/luci/luci-app-wloc-mitm_*.ipk root@192.168.1.1:/tmp/
ssh root@192.168.1.1
apk add --allow-untrusted /tmp/luci-app-wloc*.ipk   # 或 opkg install
```

### 4. 安装 mitmproxy

```sh
wloc-ctl setup
```

此步会下载并安装 mitmproxy，耗时较长。完成后 CA 证书位于 `/etc/wloc/`。

### 5. iPhone 配置

1. **安装 CA 证书**
   在 LuCI 界面「证书与 iPhone 配置」区块下载 `wloc-ca.cer`，
   发送到 iPhone（隔空投送 / 邮件 / 微信文件传输）。
   - 设置 → 通用 → VPN与设备管理 → 安装描述文件
   - 设置 → 通用 → 关于本机 → **证书信任设置** → 开启该证书的**完全信任**

2. **设置代理**
   - 设置 → 无线局域网 → 点路由器名右侧 ⓘ → 配置代理 → **手动**
   - 服务器：路由器 IP（如 `192.168.1.1`）
   - 端口：与 LuCI 中「监听端口」一致（默认 `8080`）

3. **启用改写**
   在 LuCI 界面「基本设置」中打开「启用坐标改写」，用地图选点或手填坐标，保存应用。

### 6. 启动服务

```sh
wloc-ctl enable
wloc-ctl start
wloc-ctl status
```

---

## 命令行接口

```
wloc-ctl setup       安装 mitmproxy 并生成 CA 证书
wloc-ctl status      查看服务状态、UCI 配置与统计
wloc-ctl ca          输出 CA 证书路径
wloc-ctl ca-export   输出 CA 证书内容
wloc-ctl test        用合成回包自检改包逻辑
wloc-ctl start|stop|restart|reload
wloc-ctl enable|disable
```

配置也可直接用 UCI：

```sh
uci set wloc.main.enabled='1'
uci set wloc.main.latitude='31.2304'
uci set wloc.main.longitude='121.4737'
uci set wloc.main.accuracy='25'
uci commit wloc
/etc/init.d/wloc restart
```

---

## 已知限制

- **iOS 26 及以上系统会缓存定位结果。** 切换坐标后必须**重启设备**才生效，
  飞行模式、关闭定位服务等方式都无法清除 `locationd` 的内存缓存。
  iOS 15~18 通常不需要重启。
- **只影响网络定位。** GPS 硬件定位不变；GPS 信号强时系统可能忽略网络定位结果，
  室内场景效果最佳。
- **仅 Wi-Fi 生效。** iPhone 使用蜂窝数据时不经过路由器代理。
- **需要上游连通性。** 路由器必须能访问 `gs-loc.apple.com`，
  否则 mitmproxy 无法转发真实响应（不会伪造回包）。

---

## 安全须知

信任路由器 CA 之后，**经该路由器的所有 HTTPS 流量都可以被解密**。
本项目已做以下限制：

- 只对 `gs-loc.apple.com` 与 `gs-loc-cn.apple.com` 两个域名解密，其余走裸隧道
- 统计文件不含任何定位数据，仅记录计数
- 不向任何外部服务发送数据

即便如此，仍需注意：

- 确保路由器管理密码足够强
- 不要把 LuCI 管理界面暴露到公网
- 公共 Wi-Fi 场景下慎用——同一网络内的攻击者可伪造 DHCP/DNS 诱导设备信任其他 CA

---

## 开发

### 本地测试

测试不依赖 mitmproxy，可在 PC 上直接运行：

```sh
python3 tests/test_wloc_proto.py    # protobuf 引擎（59 项）
python3 tests/test_wloc_addon.py    # addon 集成（24 项）
node    tests/test_map_adapter.js   # 地图适配层（31 项）
node    tests/test_link_parser.js   # 地图链接解析（65 项）
```

覆盖范围：varint 编解码（含负数）、protobuf 帧解析、坐标改写、MAC 守卫、
幂等性、gzip、损坏输入的容错、越界坐标拒绝、大包（256 WiFi 单元）、
两套地图 SDK 的参数差异与点击事件归一化、
Apple/Google/高德/腾讯/纯文本五类链接的坐标提取与坐标系判定。

### 目录结构

```
luci-app-wloc/
├── Makefile                     主包（运行时）
├── luci/Makefile                LuCI 界面包
├── htdocs/                      LuCI 前端资源
│   └── luci-static/resources/view/wloc.{js,css}
├── luasrc/controller/wloc.lua   CA 证书下载路由
├── root/
│   ├── etc/config/wloc          UCI 配置
│   ├── etc/init.d/wloc          procd 服务
│   ├── usr/bin/wloc-ctl         辅助命令
│   ├── usr/libexec/wloc/
│   │   ├── wloc_proto.py        protobuf 引擎（纯函数，可单测）
│   │   └── wloc_addon.py        mitmproxy 插件
│   └── usr/share/
│       ├── luci/menu.d/         菜单
│       └── rpcd/acl.d/          权限
└── tests/                       测试
```

---

## 致谢

- [Yu9191/wloc](https://github.com/Yu9191/wloc) — 原始 WLOC 定位修改思路，MIT
- [NSNanoCat/Util](https://github.com/NSNanoCat/util) — 跨平台脚本工具框架

## 许可

MIT，见 [LICENSE](LICENSE)。
