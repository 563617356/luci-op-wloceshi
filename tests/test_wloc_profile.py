"""描述文件生成器测试。

验证 plist 结构符合 iOS 要求：
  * PayloadContent 必须是 [证书, VPN] 两项
  * 证书 payload 必须是 com.apple.security.root 且含 DER 字节
  * VPN 必须是 com.apple.vpn.managed 且带 Proxies 字典
  * Proxies 必须只匹配 WLoc 域名（这是"零额外开销"的关键）
  * UUID 全局唯一且全大写（iOS 对格式敏感）
  * plist 能被 plistlib 重新解析

不依赖 openssl —— 用内置的最小 DER 证书，CI 上也能跑。

运行：python3 tests/test_wloc_profile.py
"""

import base64
import os
import plistlib
import sys
import tempfile
import textwrap
import uuid as uuid_mod

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "root", "usr", "libexec", "wloc"))

import wloc_profile  # noqa: E402

PASSED = 0
FAILURES = []


def check(cond, label):
    global PASSED
    if cond:
        PASSED += 1
        print("  ok   %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL %s" % label)


def _fake_der():
    """最小合法 DER：SEQUENCE 包裹，内含 BIT STRING + INTEGER。"""
    return bytes(
        [0x30, 0x82, 0x01, 0x0A, 0x02, 0x82, 0x01, 0x01, 0x00]
        + [0x41] * 0x101
        + [0x02, 0x03, 0x02, 0x01, 0x01, 0x30, 0x00]
    )


def make_pem():
    """造一个 PEM 证书文件。结构自洽即可，本测试不验签。"""
    der = _fake_der()
    body = base64.b64encode(der).decode()
    pem = "-----BEGIN CERTIFICATE-----\n"
    pem += "\n".join(textwrap.wrap(body, 64))
    pem += "\n-----END CERTIFICATE-----\n"
    path = os.path.join(tempfile.gettempdir(), "wloc_test_ca.pem")
    with open(path, "w") as handle:
        handle.write(pem)
    return path


def test_cert_payload():
    print("证书 payload")
    pem = make_pem()
    der = wloc_profile.read_cert_der(pem)
    check(len(der) == len(_fake_der()), "PEM 转 DER 字节数一致")
    check(der[:1] == b"\x30", "DER 以 SEQUENCE 标签开头")

    p = wloc_profile.cert_payload(der)
    check(p["PayloadType"] == "com.apple.security.root", "类型为根证书")
    check(p["PayloadContent"] == der, "内嵌 DER 原始字节")
    check(p["PayloadVersion"] == 1, "版本为 1")
    check(p["PayloadUUID"].isupper(), "UUID 全大写（iOS 敏感）")
    try:
        uuid_mod.UUID(p["PayloadUUID"])
        ok = True
    except ValueError:
        ok = False
    check(ok, "UUID 格式合法")


def test_vpn_payload():
    print("VPN payload")
    p = wloc_profile.vpn_payload("192.168.1.1", 8080)
    check(p["PayloadType"] == "com.apple.vpn.managed", "类型为 VPN 托管")
    check(p["VPNType"] == "IKEv2", "VPN 类型为 IKEv2")
    check(p["IKEv2"]["RemoteIdentifier"] == "192.168.1.1", "远端标识为路由器地址")
    check(p["IPv4"]["OverridePrimary"] == 1, "OverridePrimary 置位")

    # Proxies 是本项目的关键：装上后无需手填代理
    px = p["Proxies"]
    check(px["HTTPSEnable"] == 1, "HTTPS 代理启用")
    check(px["HTTPSProxy"] == "192.168.1.1", "代理指向路由器")
    check(px["HTTPSPort"] == 8080, "代理端口正确")
    check(px["ProxyAutoConfigEnable"] == 0, "未启用 PAC")

    # 只匹配 WLoc 域名 —— 与 WLoc8 的 matchDomains 等效
    for h in ("gs-loc.apple.com", "gs-loc-cn.apple.com"):
        check(h in px["ProxyMatchDomains"], "ProxyMatchDomains 含 %s" % h)
    check(len(px["ProxyMatchDomains"]) == 2, "仅含 2 个域名，不误伤其它流量")

    check(p["OnDemandEnabled"] == 0, "默认不启用始终打开")
    check("AlwaysOn" not in p, "默认不含 AlwaysOn 字典")


def test_always_on():
    print("AlwaysOn 模式")
    p = wloc_profile.vpn_payload("10.0.0.1", 8080, always_on=True)
    check(p["OnDemandEnabled"] == 1, "OnDemand 启用")
    check(p.get("AlwaysOn") is True, "AlwaysOn 置位")
    rules = p.get("OnDemandRules")
    check(rules and rules[0]["Action"] == "Connect", "规则为无条件 Connect")


def test_uuid_uniqueness():
    print("UUID 唯一性")
    seen = set()
    for _ in range(50):
        seen.add(wloc_profile._uuid())
    check(len(seen) == 50, "50 次生成无重复")


def test_full_profile():
    print("完整描述文件")
    pem = make_pem()
    der = wloc_profile.read_cert_der(pem)
    prof = wloc_profile.build_profile("192.168.1.1", 8080, der)

    check(prof["PayloadType"] == "Configuration", "顶层为 Configuration")
    check(len(prof["PayloadContent"]) == 2, "含 2 个 payload")

    cert, vpn = prof["PayloadContent"]
    check(cert["PayloadType"] == "com.apple.security.root", "第 1 项是根证书")
    check(vpn["PayloadType"] == "com.apple.vpn.managed", "第 2 项是 VPN")

    # VPN 必须引用同一份证书
    check(vpn["IKEv2"]["CertificateUUID"] == cert["PayloadUUID"],
          "VPN 引用的证书 UUID 与证书 payload 一致")
    check(vpn["IKEv2"]["PayloadCertificateUUID"] == cert["PayloadUUID"],
          "PayloadCertificateUUID 也已对齐")
    check("PLACEHOLDER" not in str(vpn), "占位符已全部替换")

    ids = [p["PayloadIdentifier"] for p in prof["PayloadContent"]]
    ids.append(prof["PayloadIdentifier"])
    check(len(set(ids)) == len(ids), "所有 PayloadIdentifier 互不相同")


def test_serialisation():
    print("序列化与反解析")
    pem = make_pem()
    der = wloc_profile.read_cert_der(pem)
    prof = wloc_profile.build_profile("router.lan", 8443, der)
    data = wloc_profile.dump_profile(prof)

    check(data.startswith(b"<?xml"), "输出为 XML plist")
    check(b"<!DOCTYPE plist" in data, "含 plist DOCTYPE")
    check(b"com.apple.vpn.managed" in data, "含 VPN payload 类型")
    check(len(data) > 500, "体积合理 (%d 字节)" % len(data))

    back = plistlib.loads(data)
    check(back["PayloadType"] == "Configuration", "反解析成功")
    check(len(back["PayloadContent"]) == 2, "反解析后 payload 数量一致")
    # 二进制字段在往返后应保持一致
    check(back["PayloadContent"][0]["PayloadContent"] == der,
          "证书 DER 往返无损")


def test_cli():
    print("命令行接口")
    pem = make_pem()
    out = os.path.join(tempfile.gettempdir(), "wloc_out.mobileconfig")
    rc = wloc_profile.main(
        ["--host", "192.168.1.1", "--port", "8080", "--cert", pem, "-o", out]
    )
    check(rc == 0, "退出码为 0")
    check(os.path.isfile(out) and os.path.getsize(out) > 500,
          "输出文件已生成")
    prof = plistlib.loads(open(out, "rb").read())
    check(prof["PayloadDisplayName"] == "WLOC 定位改写", "显示名正确")
    os.remove(out)

    bad = wloc_profile.main(
        ["--host", "h", "--cert", os.path.join(tempfile.gettempdir(), "nope.pem")]
    )
    check(bad == 1, "证书缺失时返回非 0")


def main():
    print("=" * 62)
    print("描述文件生成器测试")
    print("=" * 62)
    for fn in (
        test_cert_payload,
        test_vpn_payload,
        test_always_on,
        test_uuid_uniqueness,
        test_full_profile,
        test_serialisation,
        test_cli,
    ):
        fn()
        print("")

    print("=" * 62)
    if FAILURES:
        print("FAILED %d / %d" % (len(FAILURES), PASSED + len(FAILURES)))
        for n in FAILURES:
            print("  - %s" % n)
        return 1
    print("ALL %d CHECKS PASSED" % PASSED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
