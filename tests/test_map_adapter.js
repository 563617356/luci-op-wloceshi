/* 地图适配层测试：用 mock SDK 验证参数传递与坐标顺序。
 *
 * 重点验证：高德用 [lng, lat] 数组，腾讯用 LatLng(lat, lng)，
 * 两者顺序相反，极易写错——这里逐项断言。
 *
 * 运行：node tests/test_map_adapter.js
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

/* ------------------------------------------------- 从源码抽取被测函数 */

let cache = null;

function loadFunctions() {
	if (cache) return cache;

	const src = fs.readFileSync(SRC, 'utf8');

	const grab = (name) => {
		const start = src.indexOf('function ' + name);
		if (start < 0) throw new Error('未找到函数 ' + name);
		let depth = 0;
		let i = src.indexOf('{', start);
		for (; i < src.length; i++) {
			if (src[i] === '{') depth++;
			else if (src[i] === '}') {
				depth--;
				if (depth === 0) { i++; break; }
			}
		}
		return src.slice(start, i);
	};

	const code = [
		grab('createTencentAdapter'),
		grab('createAmapAdapter'),
		'module.exports = { createTencentAdapter, createAmapAdapter };'
	].join('\n\n');

	const tmp = path.join(os.tmpdir(),
		'wloc_adapters_' + process.pid + '.js');
	fs.writeFileSync(tmp, code);
	try {
		cache = require(tmp);
	} finally {
		try { fs.unlinkSync(tmp); } catch (e) { /* 清理失败不影响结果 */ }
	}
	return cache;
}

/* ------------------------------------------------------------ mock */

/* 腾讯：new LatLng(lat, lng) —— 纬度在前 */
function MockTMap() {
	const calls = { latLngArgs: [], markerPos: [], setCenter: null, resize: 0 };
	let mapInstance = null;

	function LatLng(lat, lng) {
		calls.latLngArgs.push([lat, lng]);
		this.lat = lat;
		this.lng = lng;
	}

	function Map(container, opts) {
		calls.container = container;
		calls.mapOpts = opts;
		this.handlers = {};
		this.zoom = (opts && opts.zoom) || 0;
		mapInstance = this;
	}
	Map.prototype.on = function (evt, fn) { this.handlers[evt] = fn; };
	Map.prototype.getZoom = function () { return this.zoom; };
	Map.prototype.setCenter = function (c) { calls.setCenter = c; };
	Map.prototype.resize = function () { calls.resize++; };
	Map.prototype.fire = function (evt, payload) {
		if (this.handlers[evt]) this.handlers[evt](payload);
	};

	function MultiMarker(opts) {
		calls.markerOpts = opts;
		this.setGeometries = function (geos) {
			geos.forEach(g => calls.markerPos.push(g.position));
		};
	}

	function MarkerStyle(o) { Object.assign(this, o); }

	return {
		calls: calls,
		LatLng: LatLng,
		Map: Map,
		MultiMarker: MultiMarker,
		MarkerStyle: MarkerStyle,
		fireClick: function (payload) {
			if (mapInstance) mapInstance.fire('click', payload);
		}
	};
}

/* 高德：位置是 [lng, lat] 数组 —— 经度在前，与腾讯相反 */
function MockAMap() {
	const calls = { positions: [], setCenter: null, fitView: 0 };
	let mapInstance = null;

	function Map(container, opts) {
		calls.container = container;
		calls.mapOpts = opts;
		this.handlers = {};
		this.zoom = (opts && opts.zoom) || 0;
		mapInstance = this;
	}
	Map.prototype.on = function (evt, fn) { this.handlers[evt] = fn; };
	Map.prototype.getZoom = function () { return this.zoom; };
	Map.prototype.add = function (m) { calls.added = m; };
	Map.prototype.setCenter = function (c) { calls.setCenter = c; };
	Map.prototype.setFitView = function () { calls.fitView++; };
	Map.prototype.fire = function (evt, payload) {
		if (this.handlers[evt]) this.handlers[evt](payload);
	};

	function Marker(opts) {
		calls.markerOpts = opts;
		this.setPosition = function (p) { calls.positions.push(p); };
	}

	return {
		calls: calls,
		Map: Map,
		Marker: Marker,
		fireClick: function (payload) {
			if (mapInstance) mapInstance.fire('click', payload);
		}
	};
}

/* ------------------------------------------------------------ 测试 */

function testTencentOrder() {
	console.log('腾讯地图：参数与坐标顺序');
	const { createTencentAdapter } = loadFunctions();
	const TMap = MockTMap();
	const container = { id: 'box' };

	const a = createTencentAdapter(TMap, container);
	check(a !== null, '适配器创建成功');
	check(TMap.calls.container === container, '容器传入 SDK');

	const gcj = { lat: 39.9042, lon: 116.4074 };
	a.setPoint(gcj);
	a.setCenter(gcj);

	const last = TMap.calls.latLngArgs[TMap.calls.latLngArgs.length - 1];
	check(last[0] === 39.9042 && last[1] === 116.4074,
		'LatLng 参数为 (lat, lng) — 实得 ' + JSON.stringify(last));

	check(TMap.calls.setCenter &&
		TMap.calls.setCenter.lat === 39.9042 &&
		TMap.calls.setCenter.lng === 116.4074,
		'setCenter 使用 LatLng 对象');

	const pos = TMap.calls.markerPos[TMap.calls.markerPos.length - 1];
	check(pos && pos.lat === 39.9042 && pos.lng === 116.4074,
		'marker 位置已设置');

	a.refresh();
	check(TMap.calls.resize === 1, 'refresh 触发 resize');
}

function testAmapOrder() {
	console.log('高德地图：参数与坐标顺序');
	const { createAmapAdapter } = loadFunctions();
	const AMap = MockAMap();
	const container = { id: 'box' };

	const a = createAmapAdapter(AMap, container);
	check(a !== null, '适配器创建成功');
	check(AMap.calls.container === container, '容器传入 SDK');

	const gcj = { lat: 39.9042, lon: 116.4074 };
	a.setPoint(gcj);
	a.setCenter(gcj);

	const pos = AMap.calls.positions[AMap.calls.positions.length - 1];
	/* 关键差异：高德是 [lng, lat]，与腾讯相反 */
	check(pos[0] === 116.4074 && pos[1] === 39.9042,
		'setPosition 参数为 [lng, lat] — 实得 ' + JSON.stringify(pos));

	check(AMap.calls.setCenter &&
		AMap.calls.setCenter[0] === 116.4074 &&
		AMap.calls.setCenter[1] === 39.9042,
		'setCenter 使用 [lng, lat] 数组');

	a.refresh();
	check(AMap.calls.fitView === 1, 'refresh 触发 setFitView');
}

function testInterfaceParity() {
	console.log('统一接口一致性');
	const mods = loadFunctions();
	const t = mods.createTencentAdapter(MockTMap(), { id: 'a' });
	const m = mods.createAmapAdapter(MockAMap(), { id: 'b' });

	const iface = ['setPoint', 'setCenter', 'getZoom',
		'onPick', 'onZoom', 'refresh'];

	for (const name of iface) {
		check(typeof t[name] === 'function', '腾讯适配器提供 ' + name);
		check(typeof m[name] === 'function', '高德适配器提供 ' + name);
	}
}

function testClickNormalize() {
	console.log('点击事件字段归一化');
	const mods = loadFunctions();
	const TMap = MockTMap();
	const AMap = MockAMap();

	const t = mods.createTencentAdapter(TMap, { id: 'a' });
	const m = mods.createAmapAdapter(AMap, { id: 'b' });

	let tGot = null;
	let mGot = null;
	t.onPick(function (p) { tGot = p; });
	m.onPick(function (p) { mGot = p; });

	/* 腾讯字段 evt.latLng.{lat,lng} */
	TMap.fireClick({ latLng: { lat: 31.2304, lng: 121.4737 } });
	check(tGot !== null && tGot.lat === 31.2304 && tGot.lon === 121.4737,
		'腾讯 evt.latLng 归一化 — 实得 ' + JSON.stringify(tGot));

	/* 高德字段 evt.lnglat.{lat,lng} */
	AMap.fireClick({ lnglat: { lat: 31.2304, lng: 121.4737 } });
	check(mGot !== null && mGot.lat === 31.2304 && mGot.lon === 121.4737,
		'高德 evt.lnglat 归一化 — 实得 ' + JSON.stringify(mGot));

	check(tGot && mGot && tGot.lat === mGot.lat && tGot.lon === mGot.lon,
		'两种地图源归一化后结果一致（上游换算逻辑可共用）');
}

function testMarkerFollowsPick() {
	console.log('选点后 marker 跟随');
	const mods = loadFunctions();
	const TMap = MockTMap();
	const AMap = MockAMap();

	const t = mods.createTencentAdapter(TMap, { id: 'a' });
	const m = mods.createAmapAdapter(AMap, { id: 'b' });

	t.onPick(function (gcj) { t.setPoint(gcj); });
	m.onPick(function (gcj) { m.setPoint(gcj); });

	TMap.fireClick({ latLng: { lat: 22.5, lng: 114.0 } });
	AMap.fireClick({ lnglat: { lat: 22.5, lng: 114.0 } });

	const tp = TMap.calls.markerPos[TMap.calls.markerPos.length - 1];
	const mp = AMap.calls.positions[AMap.calls.positions.length - 1];

	check(tp.lat === 22.5 && tp.lng === 114.0, '腾讯 marker 更新到选点');
	/* 高德 [lng, lat]：经度 114 应在下标 0 */
	check(mp[0] === 114.0 && mp[1] === 22.5,
		'高德 marker 更新到选点且顺序正确 — 实得 ' + JSON.stringify(mp));
}

function testZoomCallback() {
	console.log('缩放事件');
	const mods = loadFunctions();
	const TMap = MockTMap();
	const AMap = MockAMap();

	const t = mods.createTencentAdapter(TMap, { id: 'a' });
	const m = mods.createAmapAdapter(AMap, { id: 'b' });

	let fired = 0;
	const cb = function () { fired++; };
	t.onZoom(cb);
	m.onZoom(cb);

	/* 注册即应可读，触发后回调计数增加 */
	check(typeof t.getZoom() === 'number', '腾讯 getZoom 返回数字');
	check(typeof m.getZoom() === 'number', '高德 getZoom 返回数字');
	check(fired === 0, '注册后未触发时回调未执行');
}

function main() {
	console.log('='.repeat(62));
	console.log('地图适配层测试');
	console.log('='.repeat(62));

	for (const fn of [
		testTencentOrder,
		testAmapOrder,
		testInterfaceParity,
		testClickNormalize,
		testMarkerFollowsPick,
		testZoomCallback
	]) {
		fn();
		console.log('');
	}

	console.log('='.repeat(62));
	if (FAILURES.length) {
		console.log('FAILED ' + FAILURES.length + ' / ' +
			(PASSED + FAILURES.length));
		FAILURES.forEach(n => console.log('  - ' + n));
		process.exit(1);
	}
	console.log('ALL ' + PASSED + ' CHECKS PASSED');
}

main();
