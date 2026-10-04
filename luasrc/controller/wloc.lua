module("luci.controller.wloc", package.seeall)

local http = require "luci.http"
local fs = require "nixio.fs"
local sys = require "luci.sys"

local CERT_DIR = "/etc/wloc"
local CERT_FILES = {
	"mitmproxy-ca-cert.cer",
	"mitmproxy-ca-cert.pem",
}
local PROFILE_GEN = "/usr/libexec/wloc/wloc_profile.py"

local function find_cert()
	for _, name in ipairs(CERT_FILES) do
		local path = CERT_DIR .. "/" .. name
		if fs.access(path) == "r" then
			return path
		end
	end
	return nil
end

-- 仅提供 CA 证书下载。页面本身是现代 LuCI JS 视图（view/wloc.js），
-- 这里不渲染模板。
function ca()
	local found = find_cert()

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

-- 生成 iOS 描述文件（证书 + IKEv2 VPN + 代理配置）。
--
-- 装上后用户只需在「设置 → VPN」打开开关，系统会自动应用代理配置，
-- 不必再手动去 Wi-Fi 设置里填 IP 和端口。
--
-- 参数：
--   host   路由器 IP 或域名（必填）
--   port   代理端口（默认取 UCI listen_port）
--   always 1 时启用始终打开 VPN
function profile()
	local cert = find_cert()
	if not cert then
		http.status(404, "Not Found")
		http.header("Content-Type", "text/plain; charset=utf-8")
		http.write("CA 证书尚未生成，无法生成描述文件。\n")
		return
	end

	if fs.access(PROFILE_GEN, "r") ~= "r" then
		http.status(500, "Internal Server Error")
		http.write("描述文件生成器缺失: " .. PROFILE_GEN .. "\n")
		return
	end

	local uci = require "luci.model.uci"
	local cursor = uci.cursor("wloc")
	local host = http.formvalue("host")
	local port = http.formvalue("port")
	local always = http.formvalue("always")

	if not host or host == "" then
		http.status(400, "Bad Request")
		http.write("缺少 host 参数。\n")
		return
	end
	if not port or port == "" then
		port = cursor:get("main", "listen_port") or "8080"
	end

	local tmp = "/tmp/wloc-profile-" .. tostring(os.time()) .. ".mobileconfig"
	local cmd = {
		"python3", PROFILE_GEN,
		"--host", tostring(host),
		"--port", tostring(port),
		"--cert", cert,
		"-o", tmp,
	}
	if always == "1" then
		table.insert(cmd, "--always-on")
	end

	local rc = os.execute(table.concat(cmd, " "))
	if rc ~= 0 or fs.access(tmp, "r") ~= "r" then
		os.remove(tmp)
		http.status(500, "Internal Server Error")
		http.write("生成描述文件失败。\n")
		return
	end

	local handle = io.open(tmp, "r")
	if not handle then
		os.remove(tmp)
		http.status(500, "Internal Server Error")
		http.write("读取生成的描述文件失败。\n")
		return
	end
	local body = handle:read("*a")
	handle:close()
	os.remove(tmp)

	http.prepare_content("application/x-apple-aspen-config")
	http.header("Content-Disposition",
		'attachment; filename="wloc.mobileconfig"')
	http.header("Content-Length", tostring(#body))
	http.write(body)
end
