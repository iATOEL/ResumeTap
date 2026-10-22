/* 轻量 Canvas 图表：不依赖任何 CDN，保证离线与打包可用。 */
(function (global) {
  'use strict';

  const PALETTE = {
    gold: '#d9a400',
    violet: '#7c4dd6',
    slate: '#a5abb3',
    accent: '#1877f2',
    accent2: '#9360f7',
    cyan: '#1ea6c9',
    green: '#42b72a',
    red: '#e41e3f',
  };

  function setupCanvas(canvas) {
    const dpr = global.devicePixelRatio || 1;
    const parent = canvas.parentElement;
    const width = Math.max(120, parent.clientWidth || parent.getBoundingClientRect().width);
    const height = Math.max(80, parent.clientHeight || parent.getBoundingClientRect().height);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { ctx, width, height };
  }

  function readTheme() {
    const styles = getComputedStyle(document.documentElement);
    return {
      text: styles.getPropertyValue('--text').trim() || '#050505',
      dim: styles.getPropertyValue('--text-3').trim() || '#8a8d91',
      grid: '#e4e6eb',
    };
  }

  function roundRect(ctx, x, y, w, h, r) {
    const radius = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + w, y, x + w, y + h, radius);
    ctx.arcTo(x + w, y + h, x, y + h, radius);
    ctx.arcTo(x, y + h, x, y, radius);
    ctx.arcTo(x, y, x + w, y, radius);
    ctx.closePath();
  }

  /** 甜甜圈图：data = [{label, value, color}] */
  function donut(canvas, data, centerLabel) {
    const { ctx, width, height } = setupCanvas(canvas);
    const theme = readTheme();
    const total = data.reduce((sum, item) => sum + item.value, 0);
    const cx = width / 2;
    const cy = height / 2 - 6;
    const radius = Math.max(28, Math.min(width, height) / 2 - 34);
    const inner = radius * 0.62;

    if (total <= 0) {
      ctx.fillStyle = theme.dim;
      ctx.font = '13px "Microsoft YaHei UI", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('暂无数据', cx, cy);
      return;
    }

    let angle = -Math.PI / 2;
    data.forEach((item) => {
      if (item.value <= 0) return;
      const sweep = (item.value / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, radius, angle, angle + sweep);
      ctx.closePath();
      ctx.fillStyle = item.color;
      ctx.fill();
      angle += sweep;
    });

    // 内圈挖空
    ctx.beginPath();
    ctx.arc(cx, cy, inner, 0, Math.PI * 2);
    ctx.fillStyle = getComputedStyle(document.documentElement)
      .getPropertyValue('--surface').trim() || '#ffffff';
    ctx.fill();

    ctx.fillStyle = theme.text;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = '700 24px "Microsoft YaHei UI", sans-serif';
    ctx.fillText(String(total), cx, cy - 7);
    ctx.font = '11px "Microsoft YaHei UI", sans-serif';
    ctx.fillStyle = theme.dim;
    ctx.fillText(centerLabel || '总计', cx, cy + 15);

    // 图例
    let lx = 12;
    const ly = height - 12;
    ctx.font = '11px "Microsoft YaHei UI", sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    data.forEach((item) => {
      if (item.value <= 0) return;
      ctx.fillStyle = item.color;
      roundRect(ctx, lx, ly - 4, 8, 8, 2);
      ctx.fill();
      ctx.fillStyle = theme.dim;
      const label = `${item.label} ${item.value}`;
      ctx.fillText(label, lx + 12, ly);
      lx += ctx.measureText(label).width + 24;
    });
  }

  /** 柱状图：labels[], values[], colors[] */
  function bars(canvas, labels, values, colors, options) {
    const { ctx, width, height } = setupCanvas(canvas);
    const theme = readTheme();
    const opts = options || {};
    const padLeft = 34;
    const padRight = 12;
    const padTop = 14;
    const padBottom = 34;
    const chartW = width - padLeft - padRight;
    const chartH = height - padTop - padBottom;
    const maxValue = opts.max || Math.max(1, ...values);
    const count = values.length || 1;
    const slot = chartW / count;

    // 网格线
    ctx.strokeStyle = theme.grid;
    ctx.lineWidth = 1;
    ctx.font = '10px "Microsoft YaHei UI", sans-serif';
    ctx.fillStyle = theme.dim;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let i = 0; i <= 4; i += 1) {
      const y = padTop + (chartH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(width - padRight, y);
      ctx.stroke();
      const val = Math.round(maxValue - (maxValue / 4) * i);
      ctx.fillText(String(val), padLeft - 8, y);
    }

    values.forEach((value, index) => {
      const barW = Math.min(slot * 0.62, 46);
      const x = padLeft + slot * index + (slot - barW) / 2;
      const ratio = maxValue > 0 ? Math.min(1, value / maxValue) : 0;
      const barH = Math.max(2, chartH * ratio);
      const y = padTop + chartH - barH;

      const gradient = ctx.createLinearGradient(0, y, 0, y + barH);
      const color = colors[index % colors.length];
      gradient.addColorStop(0, color);
      gradient.addColorStop(1, color + '66');
      ctx.fillStyle = gradient;
      roundRect(ctx, x, y, barW, barH, 4);
      ctx.fill();

      ctx.fillStyle = theme.text;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      ctx.font = '600 11px "Microsoft YaHei UI", sans-serif';
      ctx.fillText(opts.valueFormatter ? opts.valueFormatter(value) : String(value), x + barW / 2, y - 4);

      ctx.fillStyle = theme.dim;
      ctx.textBaseline = 'top';
      ctx.font = '10px "Microsoft YaHei UI", sans-serif';
      const label = labels[index] || '';
      const maxChars = Math.max(3, Math.floor(slot / 9));
      ctx.fillText(label.length > maxChars ? label.slice(0, maxChars) + '…' : label,
        x + barW / 2, padTop + chartH + 8);
    });
  }

  /** 水平条：用于维度平均分 */
  function hbars(canvas, labels, values, color) {
    const { ctx, width, height } = setupCanvas(canvas);
    const theme = readTheme();
    const padLeft = 74;
    const padRight = 46;
    const count = Math.max(1, labels.length);
    const slot = (height - 14) / count;
    const barH = Math.min(slot * 0.5, 20);

    values.forEach((value, index) => {
      const y = 8 + slot * index + (slot - barH) / 2;
      const trackW = width - padLeft - padRight;

      ctx.fillStyle = 'rgba(120,145,200,0.14)';
      roundRect(ctx, padLeft, y, trackW, barH, barH / 2);
      ctx.fill();

      const ratio = Math.max(0, Math.min(1, value / 100));
      ctx.fillStyle = color;
      roundRect(ctx, padLeft, y, Math.max(3, trackW * ratio), barH, barH / 2);
      ctx.fill();

      ctx.fillStyle = theme.dim;
      ctx.font = '11.5px "Microsoft YaHei UI", sans-serif';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillText(labels[index], padLeft - 10, y + barH / 2);

      ctx.fillStyle = theme.text;
      ctx.font = '600 11.5px "Microsoft YaHei UI", sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText(value.toFixed(1), padLeft + trackW + 8, y + barH / 2);
    });
  }

  global.MiniChart = { donut, bars, hbars, PALETTE };
})(window);
