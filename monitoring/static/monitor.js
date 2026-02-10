async function fetchStatus() {
  try {
    const res = await fetch('/api/health');
    const data = await res.json();
    renderTable(data);
    // refresh chart data after table updates
    refreshDowntimeChart();
    refreshDeploymentsChart();
  } catch (e) {
    console.error('Failed to fetch status', e);
  }
}

function renderTable(data) {
  const tbody = document.querySelector('#status-table tbody');
  tbody.innerHTML = '';
  for (const [name, info] of Object.entries(data)) {
    const tr = document.createElement('tr');
    const nameTd = document.createElement('td');
    nameTd.textContent = name;
    const portsTd = document.createElement('td');
    if (Array.isArray(info.ports)) {
      portsTd.textContent = info.ports.join(', ');
    } else {
      portsTd.textContent = info.ports || '-';
    }
    const statusTd = document.createElement('td');
    statusTd.textContent = info.status || 'unknown';
    statusTd.className = info.status || 'unknown';
    const timeTd = document.createElement('td');
    // Format ISO timestamp to a local, human-readable string
    let human = '';
    if (info.last_checked) {
      try {
        const d = new Date(info.last_checked);
        if (!isNaN(d)) {
          const yyyy = d.getFullYear();
          const mm = String(d.getMonth() + 1).padStart(2, '0');
          const dd = String(d.getDate()).padStart(2, '0');
          const hh = String(d.getHours()).padStart(2, '0');
          const min = String(d.getMinutes()).padStart(2, '0');
          const ss = String(d.getSeconds()).padStart(2, '0');
          human = `${yyyy}-${mm}-${dd} ${hh}:${min}:${ss}`;
        } else {
          human = info.last_checked;
        }
      } catch (e) {
        human = info.last_checked;
      }
    }
    timeTd.textContent = human;
    const detailsTd = document.createElement('td');
    // Build a status text showing only uptime and status (no container name)
    let uptime = '';
    if (info.details) {
      const parts = String(info.details).split('|');
      // details format: "<uptime>|<container>" — keep only the uptime part
      if (parts.length >= 1) uptime = parts[0];
      else uptime = info.details;
    }
    let statusText = uptime || '';
    if (info.status) {
      // append status in parentheses if not already included in uptime
      if (!statusText.includes(info.status)) {
        statusText = statusText ? `${statusText} (${info.status})` : info.status;
      }
    }
    detailsTd.textContent = statusText;
    tr.appendChild(nameTd);
    tr.appendChild(portsTd);
    tr.appendChild(statusTd);
    tr.appendChild(timeTd);
    tr.appendChild(detailsTd);
    tbody.appendChild(tr);
  }
}

// Initial fetch and interval
fetchStatus();
setInterval(fetchStatus, 30000); // Refresh every 30 seconds

// Manual refresh button
const refreshBtn = document.getElementById('refresh-btn');
if (refreshBtn) {
  refreshBtn.addEventListener('click', () => {
    fetchStatus();
  });
}

// Downtime chart
let downtimeChart = null;
const rangeSelect = document.getElementById('range-select');
if (rangeSelect) {
  rangeSelect.addEventListener('change', () => refreshDowntimeChart());
}

// Also refresh deployments chart when range changes
if (rangeSelect) {
  rangeSelect.addEventListener('change', () => refreshDeploymentsChart());
}

function formatRangeLabel(iso, rangeKey) {
  try {
    const d = new Date(iso);
    if (isNaN(d)) return iso;

    const pad = (v) => String(v).padStart(2, '0');
    const mm = pad(d.getMonth() + 1);
    const dd = pad(d.getDate());
    const hh = pad(d.getHours());
    const min = pad(d.getMinutes());
    const ss = pad(d.getSeconds());

    if (rangeKey === '90d') return `${mm}-${dd}`;
    if (rangeKey === '15d' || rangeKey === '30d') return `${mm}-${dd} ${hh}:${min}`;
    if (rangeKey === '5m') return `${hh}:${min}:${ss}`;
    return `${hh}:${min}`;
  } catch (e) {
    return iso;
  }
}

async function fetchDowntime(rangeKey) {
  try {
    const res = await fetch(`/api/downtime?range=${encodeURIComponent(rangeKey)}`);
    return await res.json();
  } catch (e) {
    console.error('Failed to fetch downtime metrics', e);
    return null;
  }
}

async function refreshDowntimeChart() {
  const key = (rangeSelect && rangeSelect.value) || '12h';
  const payload = await fetchDowntime(key);
  if (!payload) return;

  const ctx = document.getElementById('downtime-chart').getContext('2d');
  const labels = payload.labels || [];
  const formattedLabels = labels.map((l) => formatRangeLabel(l, key));
  const projects = payload.projects || {};

  const datasets = Object.keys(projects).map((name, idx) => {
    const color = `hsl(${(idx * 73) % 360} 70% 50%)`;
    return {
      label: name,
      data: projects[name],
      backgroundColor: color,
    };
  });

  if (downtimeChart) {
    downtimeChart.data.labels = formattedLabels;
    downtimeChart.data.datasets = datasets;
    downtimeChart.update();
  } else {
    downtimeChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: formattedLabels,
        datasets
      },
      options: {
        responsive: true,
        plugins: {
          legend: { position: 'top' },
          title: { display: false }
        },
        scales: {
          x: { stacked: false, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 20 } },
          y: { beginAtZero: true, max: 100, title: { display: true, text: 'Downtime (%)' } }
        }
      }
    });
  }
}

// initial chart load
setTimeout(refreshDowntimeChart, 500);

// Deployments chart
let deploymentsChart = null;

async function fetchDeployments(rangeKey) {
  try {
    const res = await fetch(`/api/deployments?range=${encodeURIComponent(rangeKey)}`);
    return await res.json();
  } catch (e) {
    console.error('Failed to fetch deployments metrics', e);
    return null;
  }
}

async function refreshDeploymentsChart() {
  const key = (rangeSelect && rangeSelect.value) || '12h';
  const payload = await fetchDeployments(key);
  if (!payload) return;

  const ctx = document.getElementById('deployments-chart').getContext('2d');
  const labels = payload.labels || [];
  const formattedLabels = labels.map((l) => formatRangeLabel(l, key));
  const projects = payload.projects || {};

  const datasets = Object.keys(projects).map((name, idx) => {
    const color = `hsl(${(idx * 73) % 360} 70% 40%)`;
    return {
      label: name,
      data: projects[name],
      backgroundColor: color,
    };
  });

  if (deploymentsChart) {
    deploymentsChart.data.labels = formattedLabels;
    deploymentsChart.data.datasets = datasets;
    deploymentsChart.update();
  } else {
    deploymentsChart = new Chart(ctx, {
      type: 'bar',
      data: { labels: formattedLabels, datasets },
      options: {
        responsive: true,
        plugins: {
          legend: { position: 'top' },
          title: { display: false }
        },
        scales: {
          x: { stacked: false, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 20 } },
          y: { beginAtZero: true, title: { display: true, text: 'Deployments (count)' } }
        }
      }
    });
  }
}

// initial deployments chart load
setTimeout(refreshDeploymentsChart, 800);
