#
# luci-app-wloc-mitm — WLOC 定位改写（运行时）
#
# 提供 addon 脚本、procd 服务脚本与 wloc-ctl 辅助命令。
# 不打包 mitmproxy 本体——体积大且需针对设备架构编译，
# 由用户在设备上执行 wloc-ctl setup 自行安装。
#
include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-wloc-mitm
PKG_VERSION:=1.0.0
PKG_RELEASE:=1
PKG_LICENSE:=MIT
PKG_MAINTAINER:=563617356

include $(INCLUDE_DIR)/package.mk

define Package/luci-app-wloc-mitm
  SECTION:=net
  CATEGORY:=Network
  SUBMENU:=Proxy
  TITLE:=WLOC 定位改写运行时
  DEPENDS:=+python3-light +ca-bundle
  PKGARCH:=all
endef

define Package/luci-app-wloc-mitm/description
  Apple WLOC 网络定位坐标改写的运行时组件。

  在路由器上以 mitmproxy 插件形式拦截 gs-loc.apple.com 的定位回包，
  改写其中的 WGS-84 坐标后放行。仅对白名单内的两个域名解密，
  其余流量走裸隧道。

  安装后需执行 wloc-ctl setup 安装 mitmproxy 并生成 CA 证书。
endef

define Package/luci-app-wloc-mitm/conffiles
/etc/config/wloc
endef

define Build/Compile
endef

define Package/luci-app-wloc-mitm/install
	$(INSTALL_DIR) $(1)/usr/libexec/wloc
	$(INSTALL_BIN) ./root/usr/libexec/wloc/wloc_proto.py \
		$(1)/usr/libexec/wloc/wloc_proto.py
	$(INSTALL_BIN) ./root/usr/libexec/wloc/wloc_addon.py \
		$(1)/usr/libexec/wloc/wloc_addon.py

	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) ./root/usr/bin/wloc-ctl $(1)/usr/bin/wloc-ctl

	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) ./root/etc/init.d/wloc $(1)/etc/init.d/wloc

	$(INSTALL_DIR) $(1)/etc/config
	$(INSTALL_CONF) ./root/etc/config/wloc $(1)/etc/config/wloc

	$(INSTALL_DIR) $(1)/etc/wloc
endef

define Package/luci-app-wloc-mitm/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	echo "luci-app-wloc-mitm 已安装。"
	echo "下一步：执行 wloc-ctl setup 安装 mitmproxy 并生成 CA 证书。"
	exit 0
}
endef

$(eval $(call BuildPackage,luci-app-wloc-mitm))
