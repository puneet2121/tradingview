/* Session backgrounds use chart coordinates, so they follow zoom and pan. */
class ChartSessionBackground {
  constructor() {
    this.ranges = [];
    this.rectangles = [];
    this.colors = {};
    this.view = { zOrder: () => "bottom", renderer: () => this };
  }

  attached({ chart, requestUpdate }) {
    this.chart = chart;
    this.requestUpdate = requestUpdate;
  }

  detached() {
    this.chart = null;
    this.requestUpdate = null;
  }

  setData(ranges, colors) {
    this.ranges = ranges;
    this.colors = colors;
    if (this.requestUpdate) this.requestUpdate();
  }

  paneViews() {
    return [this.view];
  }

  updateAllViews() {
    this.rectangles = [];
    if (!this.chart) return;
    const scale = this.chart.timeScale();
    const halfBar = scale.options().barSpacing / 2;
    const width = this.chart.paneSize().width;
    for (const range of this.ranges) {
      const from = scale.timeToCoordinate(range.from);
      const to = scale.timeToCoordinate(range.to);
      if (from === null || to === null) continue;
      const left = Math.max(0, from - halfBar);
      const right = Math.min(width, to + halfBar);
      if (right > left) this.rectangles.push({ left, right, color: this.colors[range.session] });
    }
  }

  draw(target) {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      for (const rect of this.rectangles) {
        context.fillStyle = rect.color;
        context.fillRect(rect.left, 0, rect.right - rect.left, mediaSize.height);
      }
    });
  }
}

const US_SESSION_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hourCycle: "h23",
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", weekday: "short",
});

function usSessionAt(timestamp) {
  const parts = Object.fromEntries(US_SESSION_FORMATTER.formatToParts(new Date(timestamp * 1000))
    .map((part) => [part.type, part.value]));
  const minute = Number(parts.hour) * 60 + Number(parts.minute);
  let session = "closed";
  if (parts.weekday !== "Sat" && parts.weekday !== "Sun") {
    if (minute >= 240 && minute < 570) session = "pre";
    else if (minute >= 570 && minute < 960) session = "regular";
    else if (minute >= 960 && minute < 1200) session = "post";
  }
  return { session, date: `${parts.year}-${parts.month}-${parts.day}` };
}

function usSessionRanges(bars) {
  const ranges = [];
  let current = null;
  for (const bar of bars) {
    const { session, date } = usSessionAt(bar.time);
    if (session !== "pre" && session !== "post") {
      current = null;
      continue;
    }
    if (current && current.date === date && current.session === session) {
      current.to = bar.time;
    } else {
      current = { from: bar.time, to: bar.time, session, date };
      ranges.push(current);
    }
  }
  return ranges;
}
