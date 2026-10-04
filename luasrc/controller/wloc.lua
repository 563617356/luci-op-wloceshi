module("luci.controller.wloc", package.seeall)

local http = require "luci.http"
local fs = require "nixio.fs"

local CERT_DIR = "/etc/wloc"
local CERT_FILES = {
	"mitmproxy-ca-cert.cer",
	"mitmproxy-ca-cert.pem",
}

-- 仅提供 CA 证书下载。页面本身是现代 LuCI JS 视图（view/wloc.js），
-- 这里不渲染模板。
function ca()
	local found = nil

	for _, name in ipairs(CERT_FILES) do
		local path = CERT_DIR .. "/" .. name
		if fs.access(path) == "r" then
			found = path
			break
		end
	end

	if not found then
		http.status(404, "Not Found")
		http.header("Content-Type", "text/plain; charset=utf-8")
		http.write("CA 证书尚未生成。请在 SSH 下执行 wloc-ctl setup 安装 mitmproxy。\n")
		return
	end

	local handle = io.open(found, "r")
	if not handle then
		http.status(500, "Internal Server Error")
		http.write("无法读取证书文件。\n")
		return
	end

	local body = handle:read("*a")
	handle:close()

	http.prepare_content("application/x-x509-ca-cert")
	http.header("Content-Disposition", 'attachment; filename="wloc-ca.cer"')
	http.header("Content-Length", tostring(#body))
	http.write(body)
end
