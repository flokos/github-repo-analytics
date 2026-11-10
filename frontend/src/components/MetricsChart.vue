<script setup>
import {
  Chart as ChartJS,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js'
import { computed } from 'vue'
import { Line } from 'vue-chartjs'

import { useChartTheme } from '../composables/useChartTheme.js'
import { formatAxisValue, fromDayNumber, toDayNumber } from '../utils/chart.js'
import { formatCompact, formatCount, formatDate, formatShortDate } from '../utils/format.js'

// Only the pieces a line chart needs, so the rest of Chart.js is left out of the bundle.
ChartJS.register(LineController, LineElement, PointElement, LinearScale, Tooltip)

/**
 * One metric of one repository over time: a single series on a single axis.
 *
 * Stars, forks and open issues differ by orders of magnitude, so each gets a chart of its own
 * instead of sharing one plot with several y-scales.
 */
const props = defineProps({
  /** The metric's name; it also names the series, so the chart needs no legend. */
  title: { type: String, required: true },
  /** `[{ date: 'YYYY-MM-DD', value: number }]`, oldest first. */
  points: { type: Array, required: true },
  /** What the values belong to, for the accessible description. */
  subject: { type: String, default: '' },
  note: { type: String, default: '' },
})

const FONT_FAMILY = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
// Every point gets a marker while neighbours are at least this far apart; on a denser line
// only the latest point is marked.
const MIN_MARKER_SPACING = 14
// The same on every chart, so that charts stacked above each other share their x positions and
// a date can be read straight down across the three metrics.
const Y_AXIS_WIDTH = 56
const END_LABEL_SPACE = 76
const END_LABEL_MAX_LENGTH = 9

const theme = useChartTheme()

const series = computed(() =>
  props.points.map((point) => ({ x: toDayNumber(point.date), y: point.value })),
)
const latest = computed(() => props.points.at(-1) ?? null)
const endLabelText = computed(() => {
  if (!latest.value) {
    return ''
  }
  const exact = formatCount(latest.value.value)
  return exact.length <= END_LABEL_MAX_LENGTH ? exact : formatCompact(latest.value.value)
})

/** Scriptable option: Chart.js calls it per point, again after every resize. */
function markerRadius(context) {
  const count = context.dataset.data.length
  const isLatest = context.dataIndex === count - 1
  const plotWidth = context.chart.chartArea?.width ?? 0
  const hasRoom = count <= 1 || plotWidth / (count - 1) >= MIN_MARKER_SPACING
  return hasRoom || isLatest ? 5 : 0
}

const chartData = computed(() => {
  return {
    datasets: [
      {
        label: props.title,
        data: series.value,
        borderColor: theme.series,
        borderWidth: 2,
        borderJoinStyle: 'round',
        borderCapStyle: 'round',
        tension: 0,
        fill: false,
        pointRadius: markerRadius,
        pointHoverRadius: 6,
        pointHitRadius: 14,
        pointBackgroundColor: theme.series,
        pointHoverBackgroundColor: theme.series,
        // A ring in the surface colour keeps a marker legible where it sits on the line.
        pointBorderColor: theme.surface,
        pointHoverBorderColor: theme.surface,
        pointBorderWidth: 2,
        pointHoverBorderWidth: 2,
        // Let the first and last markers overlap the edge of the plot instead of being cut.
        clip: 10,
      },
    ],
  }
})

const xRange = computed(() => {
  const days = series.value.map((point) => point.x)
  const [first, last] = [days[0], days.at(-1)]
  // A single snapshot is centred instead of being stretched across the axis.
  return first === last ? { min: first - 1, max: last + 1 } : { min: first, max: last }
})

const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  layout: {
    // Room at the right for the value written at the end of the line.
    padding: { top: 10, right: END_LABEL_SPACE, bottom: 0, left: 0 },
  },
  // The pointer only has to be near a date, not on the 2px line.
  interaction: { mode: 'index', axis: 'x', intersect: false },
  plugins: {
    legend: { display: false },
    tooltip: {
      // Drawn on the chart surface so that the line key keeps the series colour.
      backgroundColor: theme.surface,
      borderColor: theme.baseline,
      borderWidth: 1,
      titleColor: theme.textSecondary,
      bodyColor: theme.textPrimary,
      // The reader already knows the series; the value is what they came for.
      titleFont: { family: FONT_FAMILY, size: 12, weight: '400' },
      bodyFont: { family: FONT_FAMILY, size: 13, weight: '600' },
      padding: 10,
      cornerRadius: 6,
      caretSize: 5,
      // The series key is a short stroke in the series colour, like the line it stands for.
      boxWidth: 14,
      boxHeight: 2,
      boxPadding: 6,
      multiKeyBackground: theme.series,
      callbacks: {
        title: (items) => formatDate(fromDayNumber(items[0].parsed.x)),
        label: (item) => `${props.title}: ${formatCount(item.parsed.y)}`,
        labelColor: () => ({ borderColor: theme.series, backgroundColor: theme.series, borderWidth: 1 }),
      },
    },
    crosshair: { color: theme.baseline },
    endLabel: { color: theme.textPrimary, text: endLabelText.value },
  },
  scales: {
    x: {
      type: 'linear',
      ...xRange.value,
      grid: { display: false },
      border: { color: theme.baseline },
      ticks: {
        color: theme.muted,
        font: { family: FONT_FAMILY, size: 11 },
        maxTicksLimit: 6,
        maxRotation: 0,
        precision: 0,
        // Evenly spaced dates only; the ends of the range are not forced in as extra ticks.
        includeBounds: false,
        callback: (value) => (Number.isInteger(value) ? formatShortDate(fromDayNumber(value)) : ''),
      },
    },
    y: {
      // A line shows change, so the axis fits the data rather than starting at zero.
      grace: '12%',
      afterFit: (axis) => {
        axis.width = Y_AXIS_WIDTH
      },
      grid: { color: theme.gridline, lineWidth: 1, drawTicks: false },
      border: { display: false },
      ticks: {
        color: theme.muted,
        font: { family: FONT_FAMILY, size: 11 },
        padding: 8,
        maxTicksLimit: 5,
        precision: 0,
        callback: (value, _index, ticks) =>
          formatAxisValue(
            value,
            ticks.map((tick) => tick.value),
          ),
      },
    },
  },
}))

/** A vertical hairline at the date under the pointer. */
const crosshairPlugin = {
  id: 'crosshair',
  beforeDatasetsDraw(chart, _args, options) {
    const active = chart.tooltip?.getActiveElements?.() ?? []
    if (active.length === 0) {
      return
    }
    const { ctx, chartArea } = chart
    const x = Math.round(active[0].element.x) + 0.5
    ctx.save()
    ctx.beginPath()
    ctx.moveTo(x, chartArea.top)
    ctx.lineTo(x, chartArea.bottom)
    ctx.lineWidth = 1
    ctx.strokeStyle = options.color
    ctx.stroke()
    ctx.restore()
  },
}

/** The latest value, written once at the end of the line (not a number on every point). */
const endLabelPlugin = {
  id: 'endLabel',
  afterDatasetsDraw(chart, _args, options) {
    const lastPoint = chart.getDatasetMeta(0).data.at(-1)
    if (!lastPoint || !options.text) {
      return
    }
    const { ctx } = chart
    ctx.save()
    ctx.font = `600 12px ${FONT_FAMILY}`
    ctx.fillStyle = options.color
    ctx.textAlign = 'left'
    ctx.textBaseline = 'middle'
    ctx.fillText(options.text, lastPoint.x + 11, lastPoint.y)
    ctx.restore()
  },
}

const chartPlugins = [crosshairPlugin, endLabelPlugin]

/** What the chart shows, in words, for readers who cannot see the canvas. */
const description = computed(() => {
  const points = props.points
  const subject = props.subject ? ` of ${props.subject}` : ''
  if (points.length === 0) {
    return `${props.title}${subject}: no data.`
  }
  const last = points.at(-1)
  if (points.length === 1) {
    return `${props.title}${subject}: ${formatCount(last.value)} on ${formatDate(last.date)}.`
  }
  const first = points[0]
  const values = points.map((point) => point.value)
  return (
    `${props.title}${subject} from ${formatDate(first.date)} to ${formatDate(last.date)}: ` +
    `${formatCount(first.value)} at the start, ${formatCount(last.value)} at the end, ` +
    `between ${formatCount(Math.min(...values))} and ${formatCount(Math.max(...values))}.`
  )
})
</script>

<template>
  <figure class="card chart">
    <figcaption class="chart__caption">
      <span class="chart__title">{{ title }}</span>
      <span v-if="note" class="muted small">{{ note }}</span>
    </figcaption>
    <div class="chart__canvas">
      <Line :data="chartData" :options="chartOptions" :plugins="chartPlugins" :aria-label="description" />
    </div>
  </figure>
</template>
