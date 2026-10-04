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

# luci.mk 默认用 LUCI_NAME?=$(notdir ${CURDIR}) 推导包名。
# 在 src-link feed 下 CURDIR 可能不含 "luci-app-wloc" 字样，
# 显式指定可避免推导失败导致包不被识别（表现为
# "No rule to make target package/luci-app-wloc/compile"）。
LUCI_NAME:=luci-app-wloc
LUCI_TITLE:=WLOC 定位改写
LUCI_DESCRIPTION:=在路由器上拦截 Apple 网络定位(Wi-Fi/基站)并改写坐标的图形界面。包含地图选点、坐标换算、运行状态与证书导出。
LUCI_DEPENDS:=+luci-base
LUCI_PKGARCH:=all

# 界面本身无 Python 依赖；Python 与 mitmproxy 在主包 luci-app-wloc-mitm 中
include $(TOPDIR)/feeds/luci/luci.mk
