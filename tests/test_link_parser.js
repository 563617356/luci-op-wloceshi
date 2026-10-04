/* 地图链接解析测试。
 *
 * 境外使用 Apple 地图是主场景，因此 Apple/Google 链接必须解析准确；
 * 境内高德/腾讯需正确识别为 GCJ-02 并反算。
 *
 * 运行：node tests/test_link_parser.js
 */

'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');

const SRC = path.join(__dirname, '..', 'htdocs', 'luci-static',
	'resources', 'view', 'wloc.js');

let PASSED = 0;
const FAILURES = [];

function check(cond, label) {
	if (cond) {
		PASSED++;
		console.log('  ok   ' + label);
	} else {
		FAILURES.push(label);
		console.log('  FAIL ' + label);
	}
}

let cache = null;

function load() {
	if (cache) return cache;
	const src = fs.readFileSync(SRC, 'utf8');

	const grab = (name) => {
		const start = src.indexOf('function ' + name);
		if (start < 0) throw new Error('未找到 ' + name);
		let depth = 0;
		let i = src.indexOf('{', start);
		for (; i < src.length; i++) {
			if (src[i] === '{') depth++;
			else if (src[i] === '}') { depth--; if (depth === 0) { i++; break; } }
		}
		return src.slice(start, i);
	};

	/* 常量也要抽出来，否则引用不到 */
	const gcjConstStart = src.indexOf('var GCJ_A');
	const gcjConstEnd = src.indexOf('function outOfChina');
	const gcjConsts = src.slice(gcjConstStart, gcjConstEnd);

	const bdConstStart = src.indexOf('var BD_A');
	const bdConstEnd = src.indexOf('function bd09ToGcj02');
	const bdConsts = src.slice(bdConstStart, bdConstEnd);

	const linkConstStart = src.indexOf('var LINK_PATTERNS');
	const linkConstEnd = src.indexOf('function parseMapLink');
	const linkConsts = src.slice(linkConstStart, linkConstEnd);

	const code = [
		gcjConsts,
		bdConsts,
		linkConsts,
		grab('outOfChina'),
		grab('deltaLat'),
		grab('deltaLon'),
		grab('wgs84ToGcj02'),
		grab('gcj02ToWgs84'),
		grab('round6'),
		grab('safeDecode'),
		grab('bd09ToGcj02'),
		grab('parseMapLink'),
		grab('toWgs84'),
		'module.exports = { parseMapLink, toWgs84, wgs84ToGcj02, gcj02ToWgs84 };'
	].join('\n\n');

	const tmp = path.join(os.tmpdir(), 'wloc_link_' + process.pid + '.js');
	fs.writeFileSync(tmp, code);
	try {
		cache = require(tmp);
	} finally {
		try { fs.unlinkSync(tmp); } catch (e) { /* ignore */ }
	}
	return cache;
}

/* ------------------------------------------------------------ Apple */

function testApple() {
	console.log('Apple 地图（境外主场景）');
	const { parseMapLink, toWgs84 } = load();

	/* iOS 分享出来的典型格式 */
	const cases = [
		['https://maps.apple.com/?ll=35.6895,139.6917&z=17',
			35.6895, 139.6917, '东京（境外，WGS-84 不变）'],
		['https://maps.apple.com/?q=35.6895,139.6917',
			35.6895, 139.6917, 'q 参数'],
		['https://maps.apple.com/?sll=35.6895,139.6917',
			35.6895, 139.6917, 'sll 参数'],
		['https://maps.apple.com/?address=Tokyo&ll=35.6895,139.6917',
			35.6895, 139.6917, '带 name/address 的分享链'],
		['https://maps.apple.com/place?name=%E6%9D%B1%E4%BA%AC%E5%A1%94&ll=35.6895,139.6917',
			35.6895, 139.6917, 'place 路径 + 中文名（URL 编码）']
	];

	for (const [url, lat, lon, label] of cases) {
		const hit = parseMapLink(url);
		check(hit !== null, '解析成功: ' + label);
		if (!hit) continue;
		check(Math.abs(hit.lat - lat) < 1e-6 && Math.abs(hit.lon - lon) < 1e-6,
			'  坐标正确 ' + hit.lat + ',' + hit.lon + ' — ' + label);
		check(hit.src === 'wgs', '  坐标系判定为 WGS-84 — ' + label);
		const w = toWgs84(hit);
		/* 境外不反算，坐标应原样 */
		check(Math.abs(w.lat - lat) < 1e-6 && Math.abs(w.lon - lon) < 1e-6,
			'  toWgs84 境外原样返回 — ' + label);
		check(w.converted === false, '  未做换算 — ' + label);
	}

	/* 地点名解码 */
	const named = parseMapLink(
		'https://maps.apple.com/place?name=Tokyo%20Station&ll=35.6812,139.7671');
	check(named && named.name === 'Tokyo Station',
		'URL 编码的地点名正确解码：' + (named && named.name));
}

/* ------------------------------------------------------------ Google */

function testGoogle() {
	console.log('Google 地图（境外）');
	const { parseMapLink, toWgs84 } = load();

	const cases = [
		['https://www.google.com/maps/@35.6895,139.6917,15z',
			35.6895, 139.6917, '@ 坐标格式'],
		['https://www.google.com/maps/place/Ginza/@35.6717,139.7650,17z',
			35.6717, 139.7650, 'place 路径带 @'],
		['https://maps.google.com/?q=35.6895,139.6917',
			35.6895, 139.6917, '?q= 格式'],
		['https://www.google.com/maps/search/Toronto/@43.6532,-79.3832,12z',
			43.6532, -79.3832, '南半球 + 西经']
	];

	for (const [url, lat, lon, label] of cases) {
		const hit = parseMapLink(url);
		check(hit !== null, '解析成功: ' + label);
		if (!hit) continue;
		check(Math.abs(hit.lat - lat) < 1e-6 && Math.abs(hit.lon - lon) < 1e-6,
			'  坐标正确 ' + hit.lat + ',' + hit.lon + ' — ' + label);
		check(hit.src === 'wgs', '  坐标系判定为 WGS-84 — ' + label);
		const w = toWgs84(hit);
		check(Math.abs(w.lat - lat) < 1e-6, '  境外原样返回 — ' + label);
	}
}

/* ------------------------------------------------------- 境内 GCJ-02 */

function testDomestic() {
	console.log('高德 / 腾讯（境内 GCJ-02）');
	const { parseMapLink, toWgs84 } = load();

	/* 高德 POI 链接 */
	const amap = parseMapLink(
		'https://www.amap.com/place/B0FFH0XXXX&p=1,B0FFH0XXXX,31.2304,121.4737,外滩,上海');
	check(amap !== null, '高德 POI 链接解析成功');
	if (amap) {
		check(Math.abs(amap.lat - 31.2304) < 1e-6, '高德纬度正确: ' + amap.lat);
		check(Math.abs(amap.lon - 121.4737) < 1e-6, '高德经度正确: ' + amap.lon);
		check(amap.src === 'gcj', '高德判定为 GCJ-02');
		const w = toWgs84(amap);
		/* 境内必须反算：结果应与原值略有差异（约 500m 量级） */
		check(w.converted === true, '境内触发坐标系换算');
		const shift = Math.abs(w.lat - amap.lat) + Math.abs(w.lon - amap.lon);
		check(shift > 1e-5 && shift < 0.02,
			'  反算偏移量在合理范围(约100~1000m)：' + shift.toFixed(6));
	}

	/* 高德 q= 链接 */
	const amapQ = parseMapLink('https://www.amap.com/search?q=31.2304,121.4737');
	check(amapQ !== null && amapQ.src === 'gcj', '高德 q= 链接判定为 GCJ-02');

	/* 腾讯地图 */
	const tencent = parseMapLink(
		'https://map.qq.com/m/place/result/city=%E6%B7%B1%E5%9C%B3&word=%E5%8D%8E%E6%99%BA%E6%96%B9%E8%88%9F&ll=22.5446,114.0579');
	check(tencent !== null, '腾讯链接解析成功');
	if (tencent) {
		check(tencent.src === 'gcj', '腾讯判定为 GCJ-02（Apple 才是 wgs）');
	}

	/* 纯文本坐标 */
	const text = parseMapLink('22.544577, 113.94114');
	check(text !== null, '纯文本坐标解析成功');
	if (text) {
		check(Math.abs(text.lat - 22.544577) < 1e-6, '纯文本纬度正确');
		check(text.src === 'gcj', '纯文本按 GCJ-02 处理（境内默认）');
	}
}

/* ------------------------------------------------------------ 异常 */

function testInvalid() {
	console.log('异常输入');
	const { parseMapLink } = load();

	const bad = [
		['', '空字符串'],
		['   ', '纯空白'],
		['https://www.google.com/maps/search/ somewhere', '无坐标的链接'],
		['hello world', '无关文本'],
		['https://example.com/?ll=999,999', '坐标越界（纬度>90）'],
		['https://example.com/?ll=91.0,200.0', '双项越界'],
		['null', '字符串 null']
	];

	for (const [input, label] of bad) {
		const hit = parseMapLink(input);
		check(hit === null, '拒绝：' + label);
	}
}

/* -------------------------------------------------------- 分享文本 */

function testShareText() {
	console.log('分享文本（夹杂说明文字）');
	const { parseMapLink } = load();

	/* iOS 分享 often 带上前后缀 */
	const share = '我在东京塔 https://maps.apple.com/?ll=35.6895,139.6917 一起去看夜景';
	const hit = parseMapLink(share);
	check(hit !== null, '从混杂文本中提取 URL');
	if (hit) {
		check(Math.abs(hit.lat - 35.6895) < 1e-6, '坐标提取正确: ' + hit.lat);
	}

	/* 中文逗号与空格分隔 */
	const cn = parseMapLink('31.2304，121.4737');
	check(cn !== null, '中文逗号分隔可用');
	const space = parseMapLink('31.2304 121.4737');
	check(space !== null, '空格分隔可用');
}

function main() {
	console.log('='.repeat(62));
	console.log('地图链接解析测试');
	console.log('='.repeat(62));

	for (const fn of [testApple, testGoogle, testDomestic, testInvalid, testShareText]) {
		fn();
		console.log('');
	}

	console.log('='.repeat(62));
	if (FAILURES.length) {
		console.log('FAILED ' + FAILURES.length + ' / ' + (PASSED + FAILURES.length));
		FAILURES.forEach(n => console.log('  - ' + n));
		process.exit(1);
	}
	console.log('ALL ' + PASSED + ' CHECKS PASSED');
}

main();
