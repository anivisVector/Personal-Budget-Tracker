/* Analytics charts (Chart.js). Colors come from CSS tokens so both themes stay
   in sync; the palette is the validated colorblind-safe pair (blue / orange). */
(function () {
  'use strict';
  const dataEl = document.getElementById('analytics-data');
  if (!dataEl || !window.Chart) return;
  const series = JSON.parse(dataEl.textContent);
  const { formatINR } = window.Ledgerly;
  const charts = [];

  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const compact = (v) => {
    const n = Math.abs(v);
    const sign = v < 0 ? '−' : '';
    if (n >= 1e7) return `${sign}₹${(n / 1e7).toFixed(1)}Cr`;
    if (n >= 1e5) return `${sign}₹${(n / 1e5).toFixed(1)}L`;
    if (n >= 1e3) return `${sign}₹${(n / 1e3).toFixed(0)}K`;
    return `${sign}₹${n.toFixed(0)}`;
  };

  function hexToRgba(hex, alpha) {
    const h = hex.replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }

  function baseOptions() {
    const grid = token('--grid');
    const muted = token('--text-3');
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? false : {
        duration: 900,
        easing: 'easeOutQuart',
        // Bars and points arrive month by month rather than all at once.
        delay: (ctx) => (ctx.type === 'data' && ctx.mode === 'default' ? ctx.dataIndex * 70 + ctx.datasetIndex * 35 : 0),
      },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false }, // the HTML legend above the chart is the identity key
        tooltip: {
          backgroundColor: token('--surface'),
          titleColor: token('--text'),
          bodyColor: token('--text-2'),
          borderColor: token('--border-strong'),
          borderWidth: 1,
          padding: 12,
          boxPadding: 4,
          usePointStyle: true,
          callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${formatINR(ctx.parsed.y, 0)}` },
        },
      },
      scales: {
        x: { grid: { display: false }, border: { color: token('--axis') }, ticks: { color: muted, font: { family: token('--font-sans') } } },
        y: {
          grid: { color: grid }, border: { display: false },
          ticks: { color: muted, callback: compact, maxTicksLimit: 6, font: { family: token('--font-sans') } },
        },
      },
    };
  }

  function render() {
    charts.forEach((c) => c.destroy());
    charts.length = 0;
    const labels = series.map((r) => r.label);
    const s1 = token('--series-1');
    const s2 = token('--series-2');
    const surface = token('--surface');

    const monthly = document.getElementById('chart-monthly');
    if (monthly) {
      const opts = baseOptions();
      opts.scales.y.beginAtZero = true;
      charts.push(new window.Chart(monthly, {
        type: 'bar',
        data: {
          labels,
          datasets: [
            { label: 'Income', data: series.map((r) => r.income), backgroundColor: s1, hoverBackgroundColor: hexToRgba(s1, 0.85), maxBarThickness: 24, borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: 'start', borderColor: surface, borderWidth: { right: 2 } },
            { label: 'Spending', data: series.map((r) => r.expense), backgroundColor: s2, hoverBackgroundColor: hexToRgba(s2, 0.85), maxBarThickness: 24, borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: 'start', borderColor: surface, borderWidth: { left: 2 } },
          ],
        },
        options: { ...opts, datasets: { bar: { categoryPercentage: 0.7, barPercentage: 0.9 } } },
      }));
    }

    const balance = document.getElementById('chart-balance');
    if (balance) {
      const opts = baseOptions();
      opts.plugins.tooltip.callbacks.label = (ctx) => ` Balance: ${formatINR(ctx.parsed.y, 0)}`;
      charts.push(new window.Chart(balance, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: 'Balance',
            data: series.map((r) => r.balance),
            borderColor: s1,
            backgroundColor: hexToRgba(s1, 0.1),
            fill: true,
            borderWidth: 2,
            tension: 0.3,
            pointRadius: 4,
            pointHoverRadius: 6,
            pointBackgroundColor: s1,
            pointBorderColor: surface,
            pointBorderWidth: 2,
          }],
        },
        options: opts,
      }));
    }
  }

  render();
  document.addEventListener('themechange', render);
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
})();
