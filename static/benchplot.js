/* Interactive gauge plot with a picture on hover (plotly).
   Used by the bench window (live plot, run detail, comparison) AND inlined into every <run>_interactive.html report,
   so the app and the report behave the same.

   BenchPlot.draw(div, series, opt)
     series: [{name, color, pts:[{x, y, img, cap, bad}]}]   img = URL of the gauge frame, cap = caption under it,
                                                            bad = draw as a black-edged X (stalled / not arrived)
     opt:    {ytitle, xtitle, dec, avg, height}              avg = moving-average window (points), 0 = none
   Safe to call again on every poll: the zoom the user chose is kept (uirevision). */
(function (g) {
  var tip = null;
  function css(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function tipEl() {
    if (tip) return tip;
    tip = document.createElement('div');
    tip.style.cssText = 'position:fixed;z-index:99999;display:none;pointer-events:none;background:#fff;color:#111;' +
      'border:1px solid #888;border-radius:8px;padding:6px;box-shadow:0 6px 20px rgba(0,0,0,.35);' +
      'font:12px system-ui,Segoe UI,sans-serif;width:300px';
    tip.innerHTML = '<img alt="gauge frame" style="display:block;width:100%;border-radius:4px;background:#222;min-height:48px">' +
      '<div style="margin-top:4px"></div>';
    document.body.appendChild(tip);
    return tip;
  }
  var last = { x: 0, y: 0 };
  function place() {
    var t = tipEl(), w = t.offsetWidth, h = t.offsetHeight, x = last.x + 18, y = last.y + 18;
    if (x + w > innerWidth - 8) x = last.x - w - 18;
    if (y + h > innerHeight - 8) y = Math.max(8, last.y - h - 18);
    t.style.left = Math.max(8, x) + 'px'; t.style.top = Math.max(8, y) + 'px';
  }
  function show(ev, url, cap) {
    var t = tipEl(), im = t.firstChild, e = ev.event || ev;
    last.x = e.clientX == null ? innerWidth / 2 : e.clientX; last.y = e.clientY == null ? 150 : e.clientY;
    im.onload = place;
    if (im.getAttribute('src') !== url) im.setAttribute('src', url || '');
    im.style.display = url ? 'block' : 'none';
    t.lastChild.textContent = cap || '';
    t.style.display = 'block';
    place();
  }
  function hide() { if (tip) tip.style.display = 'none'; }
  function rolling(pts, n) {
    var good = pts.filter(function (p) { return !p.bad; }), xs = [], ys = [];
    for (var i = 0; i < good.length; i++) {
      var a = Math.max(0, i - (n >> 1)), b = Math.min(good.length, a + n), s = 0;
      a = Math.max(0, b - n);
      for (var k = a; k < b; k++) s += good[k].y;
      xs.push(good[i].x); ys.push(s / (b - a));
    }
    return { x: xs, y: ys };
  }
  function draw(div, series, opt) {
    opt = opt || {};
    var dec = opt.dec == null ? 2 : opt.dec, fg = css('--fg') || '#222', grid = css('--line') || '#ddd', data = [];
    series.forEach(function (s) {
      if (s.mode === 'lines') {                       // time traces (hold test): no picture, plain hover value
        data.push({
          type: 'scatter', mode: 'lines', name: s.name,
          x: s.pts.map(function (p) { return p.x; }), y: s.pts.map(function (p) { return p.y; }),
          line: { color: s.color, width: 1.6, dash: s.dash || 'solid' },
          hovertemplate: '<b>%{y:.' + dec + 'f}</b> · %{x:.2f}<extra>%{fullData.name}</extra>'
        });
        return;
      }
      data.push({
        type: 'scatter', mode: 'markers', name: s.name,
        x: s.pts.map(function (p) { return p.x; }), y: s.pts.map(function (p) { return p.y; }),
        customdata: s.pts.map(function (p) { return [p.img || '', p.cap || '']; }),
        marker: {
          color: s.pts.map(function (p) { return p.bad ? '#e11d48' : s.color; }), size: s.pts.map(function (p) { return p.bad ? 10 : 7; }),
          symbol: s.pts.map(function (p) { return p.bad ? 'x' : 'circle'; }),
          line: { width: s.pts.map(function (p) { return p.bad ? 2 : 0; }), color: '#e11d48' }
        },
        hovertemplate: '<b>%{y:.' + dec + 'f}</b> · cycle %{x}<extra>%{fullData.name}</extra>'
      });
      if (opt.avg && s.pts.length > opt.avg) {
        var r = rolling(s.pts, opt.avg);
        data.push({ type: 'scatter', mode: 'lines', x: r.x, y: r.y, name: s.name + ' · ' + opt.avg + '-pt mean',
                    line: { color: s.color, width: 2 }, opacity: .85, hoverinfo: 'skip' });
      }
    });
    var layout = {
      margin: { l: 58, r: 12, t: 26, b: 42 }, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      font: { color: fg, size: 12 }, hovermode: 'closest', uirevision: 'keep',
      xaxis: { title: opt.xtitle || 'cycle', gridcolor: grid, zeroline: false },
      yaxis: { title: opt.ytitle || 'gauge reading (mm)', gridcolor: grid, zeroline: false, tickformat: '.' + dec + 'f', dtick: opt.dtick },
      legend: { orientation: 'h', y: 1.14, x: 0 }
    };
    if (opt.height) layout.height = opt.height;
    if (!data.length || !data.some(function (d) { return d.x.length; })) {
      layout.annotations = [{ text: 'no readings yet', showarrow: false, font: { color: css('--mut') || '#888', size: 15 } }];
    }
    Plotly.react(div, data, layout, { responsive: true, displaylogo: false });
    if (!div._bp) {
      div._bp = true;
      div.on('plotly_hover', function (ev) {
        var p = ev.points && ev.points[0];
        if (p && p.customdata && p.customdata[0] !== undefined) show(ev, p.customdata[0], p.customdata[1]);
      });
      div.on('plotly_unhover', hide);
    }
  }
  g.BenchPlot = { draw: draw, hide: hide };
})(window);
