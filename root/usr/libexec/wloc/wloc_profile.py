"""生成 iOS 描述文件（.mobileconfig）。

一个描述文件内含三个 payload：

  1. com.apple.security.root  — 路由器 mitmproxy 的根证书
  2. com.apple.vpn.managed     — IKEv2 VPN 配置（AlwaysOn）
  3. com.apple.dnsProxy.managed? — 不使用，DNS 走 VPN 的 DNS 设置

为什么这样能"只装一个证书"：
`com.apple.vpn.managed` 支持 `Proxies` 字典，装上后系统会在 VPN 连接时
自动应用代理设置，用户无需手动去「设置 → Wi-Fi → 代理」填 IP 和端口。
`ProxyAutoConfigEnable=0` + 显式 `HTTPSProxy/HTTPSPort` 配合
`ProxyMatchDomains` 限定只对 WLoc 域名生效，其余流量不受影响。

注意：始终打开（AlwaysOn）VPN 的 `IncludeAllNetworks` 会让**所有**流量
走隧道。这里默认关闭，由用户按需在系统设置里开 VPN——行为与手工代理
一致，但省去填参数的步骤。

用法：
    python3 wloc_profile.py --host 192.168.1.1 --port 8080 \
        --cert /etc/wloc/mitmproxy-ca-cert.cer -o wloc.mobileconfig
"""

import argparse
import base64
import plistlib
import ssl
import sys
import uuid

# 与 mitmproxy addon 保持一致的域名白名单
WLOC_HOSTS = ["gs-loc.apple.com", "gs-loc-cn.apple.com"]

BUNDLE_PREFIX = "com.wloc.router"


def _uuid():
    return str(uuid.uuid4()).upper()


def read_cert_der(path):
    """读取 PEM 证书并转成 DER（描述文件里要 DER 原始字节）。"""
    with open(path, "r") as handle:
        pem = handle.read()
    if "BEGIN CERTIFICATE" not in pem:
        raise ValueError("文件不是 PEM 证书: %s" % path)
    return ssl.PEM_cert_to_DER_cert(pem)


def cert_payload(cert_der):
    """根证书 payload —— 必须完全信任才能用于 MITM。"""
    return {
        "PayloadCertificateFileName": "wloc-mitmproxy-ca.cer",
        "PayloadContent": cert_der,
        "PayloadDescription": "WLOC 定位改写 - 根证书",
        "PayloadDisplayName": "WLOC 根证书",
        "PayloadIdentifier": "%s.root.%s" % (BUNDLE_PREFIX, _uuid()),
        "PayloadType": "com.apple.security.root",
        "PayloadUUID": _uuid(),
        "PayloadVersion": 1,
    }


def vpn_payload(host, port, always_on=False):
    """IKEv2 VPN 配置。

    这里不嵌入 PSK —— IKEv2 走证书认证时凭据由系统钥匙串管理，
    而用 SharedSecret 又要求服务端配合。路由器侧的 IPsec 服务端
    由使用者自行部署（见 README），本函数只负责把 iPhone 端配好。

    含 Proxies 字典是关键：装上后开 VPN 即自动应用代理，
    不需要用户手动填代理地址。
    """
    vpn = {
        "AuthenticationMethod": "Certificate",
        "CertificateType": "PKCS12",
        "CertificateUUID": "PLACEHOLDER",  # 由调用方替换为证书 payload UUID
        "LocalIdentifier": BUNDLE_PREFIX + ".client",
        "PayloadCertificateUUID": "PLACEHOLDER",
        "RemoteIdentifier": host,
        "ServerCertificateIssuerCommonName": host,
        "ServerCertificateCommonName": host,
        "IKESecurityAssociationParameters": {
            "EncryptionAlgorithm": "AES-256-GCM",
            "IntegrityAlgorithm": "SHA2-256",
            "DiffieHellmanGroup": 19,   # Group 19 = ECP256
            "LifeTimeInMinutes": 1440,
        },
        "DeadPeerDetectionRate": "Medium",
        "DisableMOBIKE": 0,
        "DisableRedirect": 1,
        "EnableCertificateRevocationCheck": 0,
        "EnablePFS": 0,
        "NATKeepaliveInterval": 20,
    }

    payload = {
        "IKEv2": vpn,
        "IPv4": {"OverridePrimary": 1},
        "Proxies": {
            "HTTPEnable": 0,
            "HTTPSEnable": 1,
            "HTTPSProxy": host,
            "HTTPSPort": int(port),
            "ProxyAutoConfigEnable": 0,
            "ProxyAutoDiscoveryEnable": 0,
            # 只让 WLoc 域名走代理，其余直连
            "ProxyExceptionList": list(WLOC_HOSTS),
            "ProxyMatchDomains": list(WLOC_HOSTS),
        },
        "UserDefinedName": "WLOC 定位改写",
        "VPNType": "IKEv2",
        "PayloadDescription": "安装后打开设置中的 VPN 开关即可生效",
        "PayloadDisplayName": "WLOC 定位改写",
        "PayloadIdentifier": "%s.vpn.%s" % (BUNDLE_PREFIX, _uuid()),
        "PayloadType": "com.apple.vpn.managed",
        "PayloadUUID": _uuid(),
        "PayloadVersion": 1,
        "OnDemandEnabled": 1 if always_on else 0,
    }

    if always_on:
        payload["OnDemandRules"] = [{"Action": "Connect"}]
        payload["AlwaysOn"] = True

    return payload


def build_profile(host, port, cert_der, always_on=False, name="WLOC 定位改写"):
    """组装完整描述文件。"""
    cert = cert_payload(cert_der)
    vpn = vpn_payload(host, port, always_on=always_on)

    # 让 VPN 引用的证书 UUID 指向同一份证书 payload
    vpn["IKEv2"]["CertificateUUID"] = cert["PayloadUUID"]
    vpn["IKEv2"]["PayloadCertificateUUID"] = cert["PayloadUUID"]

    return {
        "PayloadContent": [cert, vpn],
        "PayloadDisplayName": name,
        "PayloadIdentifier": "%s.profile.%s" % (BUNDLE_PREFIX, _uuid()),
        "PayloadRemovalDisallowed": False,
        "PayloadType": "Configuration",
        "PayloadUUID": _uuid(),
        "PayloadVersion": 1,
    }


def dump_profile(profile):
    """序列化为 XML plist 字节。"""
    return plistlib.dumps(profile, fmt=plistlib.FMT_XML, sort_keys=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成 WLOC iOS 描述文件")
    parser.add_argument("--host", required=True, help="路由器 IP 或域名")
    parser.add_argument("--port", type=int, default=8080, help="代理端口")
    parser.add_argument("--cert", required=True, help="根证书 PEM 路径")
    parser.add_argument("-o", "--output", help="输出文件，缺省写 stdout")
    parser.add_argument("--always-on", action="store_true",
                        help="启用始终打开 VPN（所有流量走隧道）")
    parser.add_argument("--name", default="WLOC 定位改写", help="描述文件显示名")
    args = parser.parse_args(argv)

    try:
        cert_der = read_cert_der(args.cert)
    except (OSError, ValueError) as exc:
        sys.stderr.write("读取证书失败: %s\n" % exc)
        return 1

    profile = build_profile(args.host, args.port, cert_der,
                            always_on=args.always_on, name=args.name)
    data = dump_profile(profile)

    if args.output:
        with open(args.output, "wb") as handle:
            handle.write(data)
        sys.stderr.write("已写入 %s (%d 字节)\n" % (args.output, len(data)))
    else:
        sys.stdout.buffer.write(data)

    return 0


if __name__ == "__main__":
    sys.exit(main())
