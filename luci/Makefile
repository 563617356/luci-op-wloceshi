#
# luci-app-wloc — WLOC 定位改写（LuCI 界面）
#
# 不使用 luci.mk，改为标准 package.mk + 手动安装。
#
# 原因：luci.mk 依赖 $(TOPDIR)/feeds/luci/luci.mk，且其包收集依赖
# feeds 的目录扫描。在 CI 里以 src-link 引入本地 feed 时，
# 走 luci.mk 的界面包不会被 feeds 收录（只有走 package.mk 的主包能），
# 表现为：
#   WARNING: No feed for package 'luci-app-wloc' found
#   make: *** No rule to make target 'package/luci-app-wloc/compile'
#
# 手动安装反而更可控：装什么、装到哪，都写死在下面。
#
include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-wloc
PKG_VERSION:=1.0.0
PKG_RELEASE:=1
PKG_LICENSE:=MIT
PKG_MAINTAINER:=563617356

include $(INCLUDE_DIR)/package.mk

define Package/luci-app-wloc
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=WLOC 定位改写（界面）
  DEPENDS:=+luci-base
  PKGARCH:=all
endef

define Package/luci-app-wloc/description
  在路由器上拦截 Apple 网络定位（Wi-Fi/基站）并改写坐标的图形界面。
  包含地图选点、链接导入、坐标系换算、运行状态与 CA 证书导出。
endef

define Package/luci-app-wloc/conffiles
/etc/config/wloc
endef

# 全部是预置文件，无源码需要编译
define Build/Prepare
endef

define Build/Configure
endef

define Build/Compile
endef

define Package/luci-app-wloc/install
	# ---- 前端资源 -> /www ----
	$(INSTALL_DIR) $(1)/www/luci-static/resources/view/wloc
	$(INSTALL_DATA) ./htdocs/luci-static/resources/view/wloc.js \
		$(1)/www/luci-static/resources/view/wloc/wloc.js
	$(INSTALL_DATA) ./htdocs/luci-static/resources/view/wloc.css \
		$(1)/www/luci-static/resources/view/wloc/wloc.css

	# ---- Lua 控制器 -> /usr/lib/lua/luci ----
	$(INSTALL_DIR) $(1)/usr/lib/lua/luci/controller
	$(INSTALL_DATA) ./luasrc/controller/wloc.lua \
		$(1)/usr/lib/lua/luci/controller/wloc.lua

	# ---- rpcd ACL ----
	$(INSTALL_DIR) $(1)/usr/share/rpcd/acl.d
	$(INSTALL_DATA) ./root/usr/share/rpcd/acl.d/luci-app-wloc.json \
		$(1)/usr/share/rpcd/acl.d/luci-app-wloc.json

	# ---- LuCI 菜单 ----
	$(INSTALL_DIR) $(1)/usr/share/luci/menu.d
	$(INSTALL_DATA) ./root/usr/share/luci/menu.d/luci-app-wloc.json \
		$(1)/usr/share/luci/menu.d/luci-app-wloc.json

	# ---- UCI 配置（主包也会装同一份，此处保证单独安装界面包时可用）----
	$(INSTALL_DIR) $(1)/etc/config
	$(INSTALL_CONF) ./root/etc/config/wloc $(1)/etc/config/wloc
endef

define Package/luci-app-wloc/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	rm -f /tmp/luci-indexcache.* 2>/dev/null
	rm -rf /tmp/luci-modulecache/ 2>/dev/null
	/etc/init.d/rpcd reload 2>/dev/null
	exit 0
}
endef

$(eval $(call BuildPackage,luci-app-wloc))
