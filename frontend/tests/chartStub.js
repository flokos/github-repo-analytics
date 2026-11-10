import { h } from 'vue'

/**
 * Stands in for vue-chartjs in unit tests. jsdom has no canvas, so the real chart cannot draw;
 * the stub keeps the props it was given, which is what the tests assert on.
 */
export const Line = {
  name: 'Line',
  props: {
    data: { type: Object, required: true },
    options: { type: Object, default: () => ({}) },
    plugins: { type: Array, default: () => [] },
    ariaLabel: { type: String, default: '' },
  },
  render() {
    return h('canvas', { 'data-test': 'chart', 'aria-label': this.ariaLabel })
  },
}
