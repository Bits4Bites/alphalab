// Custom scripts

/**
 * WaitTimer - displays elapsed time while waiting for AI responses.
 * Usage:
 *   const timer = new WaitTimer('wait-timer');
 *   timer.start();
 *   // ... when done:
 *   timer.stop();
 */
class WaitTimer {
    constructor(elementId) {
        this.el = document.getElementById(elementId);
        this.intervalId = null;
        this.startTime = null;
    }

    start() {
        this.startTime = Date.now();
        if (this.el) {
            this.el.textContent = '0s';
            this.el.classList.remove('d-none');
        }
        this.intervalId = setInterval(() => this._tick(), 1000);
    }

    stop() {
        if (this.intervalId) {
            clearInterval(this.intervalId);
            this.intervalId = null;
        }
        if (this.el) {
            this.el.classList.add('d-none');
        }
    }

    _tick() {
        if (!this.el || !this.startTime) return;
        const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
        if (elapsed < 60) {
            this.el.textContent = `${elapsed}s`;
        } else {
            const mins = Math.floor(elapsed / 60);
            const secs = elapsed % 60;
            this.el.textContent = `${mins}m ${secs}s`;
        }
    }
}
