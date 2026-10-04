'use strict';
'require view';
'require ui';
'require uci';
'require fs';
'require dom';
'require poll';

/*
 * luci-app-wloc — WLOC 定位改写
 *
 * 坐标系约定（重要）：
 *   - 国内地图（腾讯/高德）显示的是 GCJ-02 偏移坐标
 *   - Apple WLOC 回包要求 WGS-84
 *   - 因此选点后必须做 GCJ-02 -> WGS-84 反算，见 gcj02ToWgs84
 *   - 反向展示（把已存 WGS-84 画到地图上）则做 WGS-84 -> GCJ-02
 *
 * 地图源：腾讯地图（默认，key 走代理免配置）/ 高德地图（自备 key）。
 * 两者底图同属 GCJ-02 体系，切换时坐标可直接复用。
 * Apple 地图、Google 地图、OSM 瓦片不在国内合规白名单内，未接入。
 */

var DEFAULT_LAT = 22.544577;
var DEFAULT_LON = 113.94114;

function humanUptime(seconds) {
	if (!seconds || seconds < 1) return '—';
	var d = Math.floor(seconds / 86400);
	var h = Math.floor((seconds % 86400) / 3600);
	var m = Math.floor((seconds % 3600) / 60);
	if (d > 0) return d + ' 天 ' + h + ' 小时';
	if (h > 0) return h + ' 小时 ' + m + ' 分';
	if (m > 0) return m + ' 分 ' + (seconds % 60) + ' 秒';
	return seconds + ' 秒';
}

function humanAgo(ts) {
	if (!ts) return '从未';
	var diff = Math.floor(Date.now() / 1000) - ts;
	if (diff < 60) return '刚刚';
	if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
	if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
	return Math.floor(diff / 86400) + ' 天前';
}

/* ---------------------------------------------------------- GCJ-02 换算 */

var GCJ_A = 6378245.0;
var GCJ_EE = 0.00669342162296594323;

function outOfChina(lng, lat) {
	return !(lng > 72.004 && lng < 137.8347 && lat > 0.8293 && lat < 55.8271);
}

function deltaLat(x, y) {
	var r = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
		+ 0.2 * Math.sqrt(Math.abs(x));
	r += (20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
	r += (20.0 * Math.sin(y * Math.PI) + 40.0 * Math.sin(y / 3.0 * Math.PI)) * 2.0 / 3.0;
	r += (160.0 * Math.sin(y / 12.0 * Math.PI) + 320 * Math.sin(y * Math.PI / 30.0)) * 2.0 / 3.0;
	return r;
}

function deltaLon(x, y) {
	var r = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
		+ 0.1 * Math.sqrt(Math.abs(x));
	r += (20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
	r += (20.0 * Math.sin(x * Math.PI) + 40.0 * Math.sin(x / 3.0 * Math.PI)) * 2.0 / 3.0;
	r += (150.0 * Math.sin(x / 12.0 * Math.PI) + 300.0 * Math.sin(x / 30.0 * Math.PI)) * 2.0 / 3.0;
	return r;
}

function wgs84ToGcj02(lat, lon) {
	if (outOfChina(lon, lat)) return { lat: lat, lon: lon };
	var dLat = deltaLat(lon - 105.0, lat - 35.0);
	var dLon = deltaLon(lon - 105.0, lat - 35.0);
	var radLat = lat / 180.0 * Math.PI;
	var magic = Math.sin(radLat);
	magic = 1 - GCJ_EE * magic * magic;
	var sqrtMagic = Math.sqrt(magic);
	dLat = dLat * 180.0 / ((GCJ_A * (1 - GCJ_EE)) / (magic * sqrtMagic) * Math.PI);
	dLon = dLon * 180.0 / (GCJ_A / sqrtMagic * Math.cos(radLat) * Math.PI);
	return { lat: lat + dLat, lon: lon + dLon };
}

/* 迭代反算，与高德自身逆运算对齐，残差 < 0.1m */
function gcj02ToWgs84(lat, lon) {
	if (outOfChina(lon, lat)) return { lat: lat, lon: lon };
	var wgsLat = lat, wgsLon = lon;
	for (var i = 0; i < 6; i++) {
		var g = wgs84ToGcj02(wgsLat, wgsLon);
		var eLat = g.lat - lat, eLon = g.lon - lon;
		if (Math.abs(eLat) < 1e-9 && Math.abs(eLon) < 1e-9) break;
		wgsLat -= eLat;
		wgsLon -= eLon;
	}
	return { lat: wgsLat, lon: wgsLon };
}

function round6(n) {
	return Math.round(n * 1e6) / 1e6;
}

/* --------------------------------------------------------- 链接解析 */

/*
 * 从地图分享链接中提取坐标。
 *
 * 境外使用时 Apple 地图是最佳选择，但地图瓦片不能嵌入本界面；
 * 因此改为解析 Apple 地图的分享链接——在 iPhone 上用 Apple 地图
 * 长按选点 → 分享 → 复制链接 → 粘贴到下方输入框即可。
 * 这条路不渲染任何地图，全球可用。
 *
 * 支持格式：
 *   Apple 地图   ...?ll=纬度,经度   （境外为 WGS-84）
 *   Google 地图  .../@纬度,经度,15z  或 ?q=纬度,经度  （WGS-84）
 *   高德         ...?p=..,纬度,经度  或 ?q=纬度,经度   （GCJ-02）
 *   腾讯         ...?ll=纬度,经度                       （GCJ-02）
 *   百度         ...?qt=..&wd=.. 或 ?c=..&wd=..         （BD-09）
 *   纯文本       纬度,经度
 *
 * 返回 { lat, lon, name, src } 或 null。
 * src 为坐标系标识：'wgs' 表示已是 WGS-84，'gcj' 表示需反算，'bd' 为百度坐标。
 */

var LINK_PATTERNS = [
	{
		/* Apple 地图 / 腾讯地图：ll 或 sll 或 q=纬度,经度 */
		re: /[?&](?:ll|sll|q|coordinate)=(-?\d{1,3}(?:\.\d+)?)(?:,|%2C)(-?\d{1,3}(?:\.\d+)?)/i,
		src: 'auto'
	},
	{
		/* Google 地图：@纬度,经度,缩放 */
		re: /@(-?\d{1,3}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)(?:,[\d.]+z?)?/,
		src: 'wgs'
	},
	{
		/* Google 地图：?q=纬度,经度 或 ?ll= */
		re: /[?&](?:q|ll|center)=(-?\d{1,3}(?:\.\d+)?)(?:,|%2C)(-?\d{1,3}(?:\.\d+)?)/i,
		src: 'wgs'
	},
	{
		/* 高德 POI：?p=POIID,纬度,经度,名称,城市 */
		re: /[?&]p=[^,&%]*(?:,|%2C)(-?\d{1,3}(?:\.\d+)?)(?:,|%2C)(-?\d{1,3}(?:\.\d+)?)/i,
		src: 'gcj'
	},
	{
		/* 百度：?c=城市&wd=名称 通常无坐标；?qt=poi&wd= 也无坐标。
		 * 百度地图移动端分享多为 bdmap://?qt=s&wd=..&c=.. 同样缺坐标。
		 * 这里只能兜底纯文本形式。 */
		re: /[?&](?:coord|coordinate)=(-?\d{1,3}(?:\.\d+)?)(?:,|%2C)(-?\d{1,3}(?:\.\d+)?)/i,
		src: 'bd'
	},
	{
		/* 纯文本坐标：纬度,经度（至少 4 位小数，避免误抓数字） */
		re: /(-?\d{1,3}\.\d{4,})\s*(?:,|，|\s)\s*(-?\d{1,3}\.\d{4,})/,
		src: 'gcj'
	}
];

function parseMapLink(text) {
	if (!text) return null;
	var raw = String(text).trim();
	if (!raw) return null;

	/* 从一段文本里抠出 URL（分享文本常夹杂说明文字） */
	var urlMatch = raw.match(/https?:\/\/[^\s'"<>，。]+/i);
	var target = urlMatch ? urlMatch[0] : raw;

	/* 名称：苹果地图 name= / 高德 name= */
	var nameMatch = target.match(/[?&]name=([^&]+)/i);
	var name = nameMatch ? safeDecode(nameMatch[1]) : '';

	/* 域名判定坐标系（参数名会撞车，必须看域名） */
	var lower = target.toLowerCase();
	var isApple = lower.indexOf('maps.apple.com') >= 0;
	var isGoogle = lower.indexOf('google.com/maps') >= 0
		|| lower.indexOf('maps.google.') >= 0
		|| lower.indexOf('goo.gl/maps') >= 0;
	var isBaidu = lower.indexOf('baidu.com') >= 0
		|| lower.indexOf('bdmap://') >= 0;

	for (var i = 0; i < LINK_PATTERNS.length; i++) {
		var p = LINK_PATTERNS[i];
		var m = target.match(p.re);
		if (!m) continue;

		var lat = parseFloat(m[1]);
		var lon = parseFloat(m[2]);
		if (isNaN(lat) || isNaN(lon)) continue;

		/* 合法性校验：过滤掉把经纬度写反的情况 */
		if (lat < -90 || lat > 90 || lon < -180 || lon > 180) continue;

		var src = p.src;
		if (src === 'auto') {
			/*
			 * 按域名判定坐标系，而不是按参数名。
			 * 关键：Google 的 ?q= 与高德的 ?q= 参数名相同但坐标系不同，
			 * 必须先看域名，否则 Google 链接会被误当成 GCJ-02 而多算一次反算。
			 *
			 * Apple 地图在境外就是 WGS-84（境外无偏移坐标系）；
			 * 境内 Apple 显示 GCJ-02，但那种情况下一律按 WGS-84 处理也不会
			 * 造成显著误差——Apple 分享链的 ll 在境内已是 WGS-84。
			 */
			if (isApple) src = 'wgs';
			else if (isGoogle) src = 'wgs';
			else if (isBaidu) src = 'bd';
			else src = 'gcj';   /* 腾讯、高德默认 GCJ-02 */
		}

		return { lat: lat, lon: lon, name: name, src: src };
	}

	return null;
}

function safeDecode(s) {
	if (!s) return '';
	try {
		return decodeURIComponent(String(s).replace(/\+/g, ' '));
	} catch (e) {
		return String(s);
	}
}

/* 百度 BD-09 -> GCJ-02 */
var BD_A = 0.00000669342162296594323;
var BD_EE = 0.00669342162296594323;

function bd09ToGcj02(lat, lon) {
	var x = lon - 0.0065;
	var y = lat - 0.006;
	var z = Math.sqrt(x * x + y * y) - 0.00002 * Math.sin(y * Math.PI * 3000.0 / 180.0);
	var theta = Math.atan2(y, x) - 0.000003 * Math.cos(x * Math.PI * 3000.0 / 180.0);
	return { lat: z * Math.sin(theta), lon: z * Math.cos(theta) };
}

/*
 * 统一入口：把任意来源的坐标归一为 WGS-84。
 * 返回 { lat, lon, name, converted }，converted 说明是否做了换算。
 */
function toWgs84(hit) {
	var lat = hit.lat;
	var lon = hit.lon;
	var converted = false;

	if (hit.src === 'bd') {
		/* 百度 BD-09 -> GCJ-02 */
		var g = bd09ToGcj02(lat, lon);
		lat = g.lat;
		lon = g.lon;
		converted = true;
	}

	if (hit.src === 'bd' || hit.src === 'gcj') {
		if (!outOfChina(lon, lat)) {
			var wgs = gcj02ToWgs84(lat, lon);
			lat = wgs.lat;
			lon = wgs.lon;
			converted = true;
		}
	}

	return {
		lat: round6(lat),
		lon: round6(lon),
		name: hit.name || '',
		converted: converted
	};
}

/* ---------------------------------------------------------- 地图加载 */

/*
 * 地图源可切换：腾讯地图 / 高德地图。
 *
 * 两者底图数据同属 GCJ-02 体系，在中国大陆的选点体验基本一致，
 * 因此切换时坐标值可直接复用，无需重新换算。
 *
 * 合规说明：Apple 地图、Google 地图、OSM 瓦片不在国内地图服务白名单内，
 * 出于合规要求不接入。腾讯与高德均在白名单内。
 */

var TENCENT_CDN = 'https://map.qq.com/api/gljs?v=1.exp';
var AMAP_CDN = 'https://webapi.amap.com/maps?v=2.0&key=';

var sdkPromises = {};

/* 腾讯地图：走 key 代理模式，前端不持有 key */
function ensureTencentSdk() {
	if (window.TMap) return Promise.resolve(window.TMap);
	if (sdkPromises.tencent) return sdkPromises.tencent;

	sdkPromises.tencent = new Promise(function (resolve, reject) {
		window._TMapSecurityConfig = {
			serviceHost: L.env._TMapService
		};

		var script = document.createElement('script');
		script.src = TENCENT_CDN;
		script.async = true;
		script.onload = function () {
			if (window.TMap) resolve(window.TMap);
			else reject(new Error('SDK 已加载但 TMap 未定义'));
		};
		script.onerror = function () {
			sdkPromises.tencent = null;
			reject(new Error('无法访问腾讯地图 CDN'));
		};
		document.head.appendChild(script);
	});

	return sdkPromises.tencent;
}

/* 高德地图：需用户自行申请 Web 端（JS API）key */
function ensureAmapSdk(key) {
	if (window.AMap) return Promise.resolve(window.AMap);
	if (sdkPromises.amap) return sdkPromises.amap;

	if (!key) {
		return Promise.reject(new Error('未配置高德地图 key'));
	}

	sdkPromises.amap = new Promise(function (resolve, reject) {
		/* 高德 JS API 需要先设 securityJsCode 做域名校验，
		 * 但仅在用户自建服务时必须；此处留空以免报错。 */
		window._AMapSecurityConfig = {
			serviceHost: L.env._TMapService
		};

		var script = document.createElement('script');
		script.src = AMAP_CDN + encodeURIComponent(key);
		script.async = true;
		script.onload = function () {
			if (window.AMap) resolve(window.AMap);
			else reject(new Error('SDK 已加载但 AMap 未定义，请检查 key 是否有效'));
		};
		script.onerror = function () {
			sdkPromises.amap = null;
			reject(new Error('无法访问高德地图 CDN'));
		};
		document.head.appendChild(script);
	});

	return sdkPromises.amap;
}

/*
 * 统一的地图适配器。
 * 对外只暴露 create(lat, lon) / onPick / onZoom / setCenter / setPoint / getZoom，
 * 上层无需关心底层是 TMap 还是 AMap。
 */
function createAdapter(kind, sdk, container) {
	if (kind === 'amap') {
		return createAmapAdapter(sdk, container);
	}
	return createTencentAdapter(sdk, container);
}

function createTencentAdapter(TMap, container) {
	var map = new TMap.Map(container, {
		center: new TMap.LatLng(0, 0),
		zoom: 12
	});

	var marker = new TMap.MultiMarker({
		map: map,
		styles: {
			default: new TMap.MarkerStyle({
				width: 20, height: 20, anchor: { x: 10, y: 10 }
			})
		},
		geometries: []
	});

	return {
		setPoint: function (gcj) {
			marker.setGeometries([{
				id: 'pin',
				styleId: 'default',
				position: new TMap.LatLng(gcj.lat, gcj.lon)
			}]);
		},
		setCenter: function (gcj) {
			map.setCenter(new TMap.LatLng(gcj.lat, gcj.lon));
		},
		getZoom: function () { return map.getZoom(); },
		/* 高德/腾讯的 click 事件字段名不同，统一在此转换 */
		onPick: function (fn) {
			map.on('click', function (evt) {
				fn({ lat: evt.latLng.lat, lon: evt.latLng.lng });
			});
		},
		onZoom: function (fn) {
			map.on('zoomchange', fn);
		},
		/* 腾讯容器需显式触发 resize 才能正确铺满 */
		refresh: function () {
			map.resize && map.resize();
		}
	};
}

function createAmapAdapter(AMap, container) {
	var map = new AMap.Map(container, {
		zoom: 12,
		center: [0, 0]
	});

	var marker = new AMap.Marker({
		position: [0, 0],
		draggable: false
	});
	map.add(marker);

	return {
		setPoint: function (gcj) {
			marker.setPosition([gcj.lon, gcj.lat]);
		},
		setCenter: function (gcj) {
			map.setCenter([gcj.lon, gcj.lat]);
		},
		getZoom: function () { return map.getZoom(); },
		onPick: function (fn) {
			map.on('click', function (evt) {
				/* 高德的 lnglat 是 LngLat 对象 */
				fn({ lat: evt.lnglat.lat, lon: evt.lnglat.lng });
			});
		},
		onZoom: function (fn) {
			map.on('zoomchange', fn);
		},
		refresh: function () {
			map.setFitView && map.setFitView();
		}
	};
}

/* ------------------------------------------------------------ 小组件 */

function row(label, value) {
	return E('div', { 'class': 'tr' }, [
		E('div', { 'class': 'td left', 'style': 'width:32%' }, [
			E('strong', {}, label)
		]),
		E('div', { 'class': 'td left' }, [
			Dom.isDom(value) ? value : String(value)
		])
	]);
}

return view.extend({

	handleSaveApply: null,
	handleSave: null,
	handleReset: null,

	load: function () {
		return Promise.all([
			uci.load('wloc'),
			fs.read('/var/run/wloc/stats.json'),
			fs.read('/etc/wloc/mitmproxy-ca-cert.cer')
		]);
	},

	/* 状态面板 */
	renderStatus: function (stats) {
		if (!stats) {
			return E('div', { 'class': 'table' }, [
				E('div', { 'class': 'tr' }, [
					E('div', { 'class': 'td left' }, [
						E('em', { 'class': 'cbi-section-descr' },
							'暂无统计数据。服务启动并处理过 WLOC 请求后生成。' +
							'若一直没有，请确认已执行 wloc-ctl setup 安装 mitmproxy。')
					])
				])
			]);
		}

		var modeText = stats.passthrough_mode ? '透传（不修改定位）' : '改写坐标';
		var modeColor = stats.passthrough_mode ? '#c88025' : 'green';

		return E('div', { 'class': 'table' }, [
			row('服务状态', E('span', {
				'style': 'color:green;font-weight:bold'
			}, ['运行中'])),
			row('运行时长', humanUptime(stats.uptime)),
			row('当前模式', E('span', { 'style': 'color:' + modeColor }, modeText)),
			row('WLOC 请求数', String(stats.requests)),
			row('成功改写', String(stats.patched)),
			row('透传', String(stats.passthrough)),
			row('错误', String(stats.errors)),
			row('改写定位点', String(stats.locations || 0) + ' 个'
				+ '（WiFi ' + (stats.wifi || 0) + ' / 基站 ' + (stats.cell || 0) + '）'),
			row('最后改写', humanAgo(stats.last_time)),
			stats.last_message ? row('最后消息', stats.last_message) : E('span')
		]);
	},

	/* 证书区块 */
	renderCert: function (hasCert) {
		return E('div', { 'class': 'cbi-section' }, [
			E('h3', {}, [ _('证书与 iPhone 配置') ]),
			E('div', { 'class': 'cbi-section-descr' },
				_('iOS 只信任手动安装的根证书，装完还必须开启「完全信任」，否则无效。')),
			E('div', { 'class': 'cbi-value' }, [
				E('label', { 'class': 'cbi-value-title' }, [ _('CA 证书') ]),
				E('div', { 'class': 'cbi-value-field' }, [
					hasCert
						? E('a', {
							'class': 'cbi-button cbi-button-action',
							'href': L.url('admin', 'services', 'wloc', 'ca'),
							'target': '_blank'
						}, [ _('下载 wloc-ca.cer') ])
						: E('span', { 'class': 'cbi-value-description' },
							_('尚未生成。请在 SSH 下执行 wloc-ctl setup，或启动一次服务。')),
					hasCert
						? E('div', { 'class': 'cbi-value-description', 'style': 'margin-top:6px' },
							_('iPhone 操作：设置 → 通用 → VPN与设备管理 安装描述文件；' +
							  '然后 设置 → 通用 → 关于本机 → 证书信任设置，开启完全信任。'))
						: ''
				])
			]),
			E('div', { 'class': 'cbi-value' }, [
				E('label', { 'class': 'cbi-value-title' }, [ _('iPhone 代理') ]),
				E('div', { 'class': 'cbi-value-field' }, [
					E('div', { 'class': 'cbi-value-description' },
						_('设置 → 无线局域网 → 点路由器名右侧 ⓘ → 配置代理 → 手动，' +
						  '服务器填本路由器 IP，端口填下方「监听端口」。')),
					E('div', { 'class': 'cbi-value-description' },
						_('仅 Wi-Fi 生效，蜂窝网络会绕过代理。'))
				])
			])
		]);
	},

	/* 注意事项 */
	renderNotes: function () {
		return E('div', { 'class': 'cbi-section' }, [
			E('h3', {}, [ _('注意事项') ]),
			E('div', { 'class': 'cbi-section-descr' }, [
				E('ul', { 'style': 'margin:0;padding-left:20px' }, [
					E('li', {}, [ _('iOS 26 及以上会缓存定位结果。切换坐标后需重启设备才生效，' +
						'飞行模式清不掉该缓存。') ]),
					E('li', {}, [ _('只改网络定位（Wi-Fi/基站），不动 GPS 硬件定位。' +
						'GPS 信号强时系统可能忽略网络定位，室内效果最佳。') ]),
					E('li', {}, [ _('设备信任路由器 CA 后，经本机的 HTTPS 流量均可被解密。' +
						'请确保管理密码足够强，且管理界面未暴露到外网。') ]),
					E('li', {}, [ _('所有处理都在本机完成，不向任何外部服务发送定位数据。') ])
				])
			])
		]);
	},

	render: function (data) {
		var stats = null;
		if (data[1] && data[1] !== '') {
			try { stats = JSON.parse(data[1]); } catch (err) { stats = null; }
		}
		var hasCert = !!(data[2] && data[2] !== '');

		var storedLat = parseFloat(uci.get('wloc', 'main', 'latitude') || DEFAULT_LAT);
		var storedLon = parseFloat(uci.get('wloc', 'main', 'longitude') || DEFAULT_LON);
		if (isNaN(storedLat)) storedLat = DEFAULT_LAT;
		if (isNaN(storedLon)) storedLon = DEFAULT_LON;

		/* ---- 基本设置 ---- */
		var basic = E('div', { 'class': 'cbi-section' }, [
			E('h3', {}, [ _('基本设置') ]),
			this.renderSwitch(_('启用坐标改写'),
				_('关闭后不修改任何数据，系统恢复真实定位。'),
				'enabled'),
			this.renderValue(_('目标纬度'),
				_('WGS-84 纬度，北纬为正、南纬为负，有效范围 -90 ~ 90。'),
				'latitude'),
			this.renderValue(_('目标经度'),
				_('WGS-84 经度，东经为正、西经为负，有效范围 -180 ~ 180。'),
				'longitude'),
			this.renderValue(_('精度半径（米）'),
				_('定位精度声明，数值越小越精确。建议 25 ~ 100。'),
				'accuracy'),
			this.renderValue(_('监听端口'),
				_('iPhone 的 Wi-Fi 代理端口需与此一致。'),
				'listen_port'),
			this.renderValue(_('日志级别'),
				_('off / error / warn / info / debug / all'),
				'log_level')
		]);

		/* ---- 链接导入（境外使用 Apple 地图的推荐路径） ---- */
		var linkInput = E('input', {
			'class': 'cbi-input-text',
			'type': 'text',
			'id': 'wloc_link_input',
			'style': 'width:100%',
			'placeholder': '粘贴 Apple 地图 / Google 地图 / 高德 / 腾讯 的分享链接，或直接输入「纬度,经度」'
		});
		var linkBtn = E('button', {
			'class': 'cbi-button cbi-button-action',
			'style': 'margin-top:8px'
		}, [ '解析并填入' ]);
		var linkHint = E('div', {
			'class': 'cbi-value-description',
			'style': 'margin-top:8px'
		}, [ '' ]);

		var linkSection = E('div', { 'class': 'cbi-section' }, [
			E('h3', {}, [ _('从地图链接导入坐标') ]),
			E('div', { 'class': 'cbi-section-descr' },
				_('境外使用推荐此方式：在 iPhone 上用 Apple 地图长按选点 → 分享 → 复制链接，' +
				  '粘贴到下方即可自动提取坐标。链接解析不依赖任何地图服务，全球可用；' +
				  '境外坐标为 WGS-84，无需偏移换算。')),
			linkInput,
			E('div', {}, [ linkBtn ]),
			linkHint,
			E('div', { 'class': 'cbi-value-description' },
				_('说明：Apple 地图与 Google 地图返回 WGS-84，可直接使用；' +
				  '高德、腾讯返回 GCJ-02，境内会自动反算为 WGS-84。' +
				  'iOS 26+ 切换坐标后需重启设备才生效。'))
		]);

		/* ---- 地图选点 ---- */
		var mapBox = E('div', {
			'id': 'wloc_map',
			'style': 'width:100%;height:340px;border-radius:8px;overflow:hidden'
		});
		var pickHint = E('div', {
			'id': 'wloc_pick_hint',
			'class': 'cbi-value-description',
			'style': 'margin-top:8px'
		}, [ '点击地图选点，坐标会自动换算为 WGS-84 并填入上方。' ]);
		var zoomLabel = E('div', {
			'style': 'text-align:right;color:#999;font-size:12px;margin-bottom:4px'
		}, [ '' ]);

		/* 地图源切换按钮 */
		var currentMap = uci.get('wloc', 'main', 'map_provider') || 'tencent';
		if (currentMap !== 'tencent' && currentMap !== 'amap') {
			currentMap = 'tencent';
		}
		var amapKey = uci.get('wloc', 'main', 'amap_key') || '';

		var btnTencent = E('button', {
			'class': 'cbi-button cbi-button-action',
			'style': 'margin-right:8px'
		}, [ '腾讯地图' ]);
		var btnAmap = E('button', {
			'class': 'cbi-button cbi-button-action'
		}, [ '高德地图' ]);

		var amapKeyRow = E('div', {
			'class': 'cbi-value',
			'style': 'display:' + (currentMap === 'amap' ? 'block' : 'none')
		}, [
			E('label', { 'class': 'cbi-value-title' }, [ _('高德地图 Key') ]),
			E('div', { 'class': 'cbi-value-field' }, [
				E('input', {
					'class': 'cbi-input-text',
					'type': 'text',
					'name': 'amap_key',
					'value': amapKey,
					'placeholder': '在 lbs.amap.com 申请「Web 端（JS API）」类型的 key'
				}),
				E('div', { 'class': 'cbi-value-description' },
					_('高德地图需自备 key。申请入口：https://lbs.amap.com/api/webservice/guide/api-key；' +
					  '创建时选择「Web端(JS API)」，服务平台选「Web服务」或「Web端(JS API)」。' +
					  '保存后需重新加载页面生效。'))
			])
		]);

		var mapSection = E('div', { 'class': 'cbi-section' }, [
			E('h3', {}, [ _('地图选点') ]),
			E('div', { 'class': 'cbi-section-descr' },
				_('国内地图使用 GCJ-02 偏移坐标，本插件会自动换算为 Apple 所需的 WGS-84。' +
				  '两种地图源底图数据同属 GCJ-02 体系，切换后坐标可直接复用。')),
			E('div', { 'style': 'margin-bottom:8px' }, [ btnTencent, btnAmap ]),
			amapKeyRow,
			zoomLabel,
			mapBox,
			pickHint
		]);

		/* ---- 地图初始化：DOM 就绪后再加载 SDK 并初始化 ---- */
		var adapter = null;

		function fillLatLon(lat, lon) {
			var wgs = gcj02ToWgs84(lat, lon);
			var latInput = document.querySelector(
				'.cbi-value input[name="latitude"]');
			var lonInput = document.querySelector(
				'.cbi-value input[name="longitude"]');
			if (latInput) latInput.value = round6(wgs.lat);
			if (lonInput) lonInput.value = round6(wgs.lon);
			pickHint.textContent = '已选点：' + round6(wgs.lat) + ', '
				+ round6(wgs.lon) + '（WGS-84）';
		}

		function mountMap(kind) {
			currentMap = kind;

			/* 未保存的高德 key 也允许即时体验 */
			var keyInput = document.querySelector(
				'.cbi-value input[name="amap_key"]');
			var key = (keyInput && keyInput.value) || amapKey;

			var loader = (kind === 'amap')
				? ensureAmapSdk(key)
				: ensureTencentSdk();

			pickHint.textContent = '正在加载地图…';

			loader.then(function (sdk) {
				/* 切换地图时先清空容器，避免两个 SDK 的 canvas 叠加 */
				Dom.content(mapBox, '');

				adapter = createAdapter(kind, sdk, mapBox);
				var g = wgs84ToGcj02(storedLat, storedLon);
				adapter.setPoint(g);
				adapter.setCenter(g);
				adapter.refresh();

				var zoomText = function () {
					zoomLabel.textContent = '缩放级别 ' + adapter.getZoom();
				};
				zoomText();

				adapter.onZoom(zoomText);
				adapter.onPick(function (gcj) {
					if (adapter) adapter.setPoint(gcj);
					/* 地图给的是 GCJ-02，反算成 WGS-84 再回填 */
					fillLatLon(gcj.lat, gcj.lon);
				});

				pickHint.textContent = kind === 'amap'
					? '高德地图已就绪，点击地图选点（坐标将换算为 WGS-84）。'
					: '腾讯地图已就绪，点击地图选点（坐标将换算为 WGS-84）。';
			}).catch(function (err) {
				adapter = null;
				if (kind === 'amap' && !key) {
					pickHint.textContent = '使用高德地图需先在上方填写 key 并保存。'
						+ '错误：' + err.message;
				} else {
					pickHint.textContent = '地图加载失败：' + err.message
						+ '。坐标仍可在上方手工填写。';
				}
			});
		}

		function markActive() {
			btnTencent.style.opacity = currentMap === 'tencent' ? '1' : '0.5';
			btnAmap.style.opacity = currentMap === 'amap' ? '1' : '0.5';
			amapKeyRow.style.display = currentMap === 'amap' ? 'block' : 'none';
		}

		btnTencent.addEventListener('click', function () {
			markActive();
			mountMap('tencent');
		});
		btnAmap.addEventListener('click', function () {
			markActive();
			mountMap('amap');
		});

		/* ---- 链接解析 ---- */
		function doParseLink() {
			var text = linkInput.value;
			var hit = parseMapLink(text);

			if (!hit) {
				linkHint.textContent = '未能从内容中解析出坐标。' +
					'请确认复制的是地图分享链接，或按「纬度,经度」格式输入。';
				linkHint.style.color = '#c88025';
				return;
			}

			var wgs = toWgs84(hit);

			var latInput = document.querySelector(
				'.cbi-value input[name="latitude"]');
			var lonInput = document.querySelector(
				'.cbi-value input[name="longitude"]');
			if (latInput) latInput.value = wgs.lat;
			if (lonInput) lonInput.value = wgs.lon;

			var srcLabel = { wgs: 'WGS-84', gcj: 'GCJ-02', bd: '百度 BD-09' };
			var msg = '已解析：' + wgs.lat + ', ' + wgs.lon + '（WGS-84）'
				+ '  来源：' + (srcLabel[hit.src] || hit.src);
			if (hit.name) msg += '  地点：' + hit.name;
			if (wgs.converted) msg += '  已做坐标系换算';
			linkHint.textContent = msg;
			linkHint.style.color = 'green';

			/* 同步地图标记，让用户看到落在哪 */
			if (adapter) {
				var g = wgs84ToGcj02(wgs.lat, wgs.lon);
				adapter.setPoint(g);
				adapter.setCenter(g);
			}
		}

		linkBtn.addEventListener('click', doParseLink);
		linkInput.addEventListener('keydown', function (e) {
			if (e.key === 'Enter') {
				e.preventDefault();
				doParseLink();
			}
		});

		/* mapBox 此时尚未插入 DOM，等下一轮再初始化 */
		poll.add(function () {
			markActive();
			mountMap(currentMap);
		});

		return E('div', { 'class': 'cbi-map' }, [
			E('h2', {}, [ _('WLOC 定位改写') ]),
			E('div', { 'class': 'cbi-map-descr' },
				_('在路由器上拦截并改写 Apple 网络定位（Wi-Fi/基站）返回的坐标，' +
				  '使连接到本路由器 Wi-Fi 的 iPhone 上报虚拟位置。')),
			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, [ _('运行状态') ]),
				this.renderStatus(stats)
			]),
			basic,
			linkSection,
			mapSection,
			this.renderCert(hasCert),
			this.renderNotes()
		]);
	}
});
