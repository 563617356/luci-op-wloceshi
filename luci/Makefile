#
# luci-app-wloc — WLOC 定位改写（LuCI 界面）
#
# luci.mk 会自动打包以下目录，无需在 Makefile 中逐一声明：
#   luasrc/   -> /usr/lib/lua/luci/
#   htdocs/   -> /www/
#   root/     -> / （含 rpcd ACL 与菜单定义）
#
include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-wloc
PKG_VERSION:=1.0.0
PKG_RELEASE:=1
PKG_LICENSE:=MIT
PKG_MAINTAINER:=563617356

LUCI_TITLE:=WLOC 定位改写
LUCI_DESCRIPTION:=在路由器上拦截 Apple 网络定位(Wi-Fi/基站)并改写坐标的图形界面。包含地图选点、坐标换算、运行状态与证书导出。
LUCI_DEPENDS:=+luci-base
LUCI_PKGARCH:=all

# 界面本身无 Python 依赖；Python 与 mitmproxy 在主包 luci-app-wloc-mitm 中
include $(TOPDIR)/feeds/luci/luci.mk
