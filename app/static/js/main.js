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

/**
 * Pre-process raw markdown text to normalize LaTeX delimiters before marked.parse().
 * Converts common AI-generated patterns:
 *   [ ... ] (display math with LaTeX commands) → $$ ... $$
 *   \[ ... \] → $$ ... $$
 */
function prepareMath(text) {
    if (!text) return text;
    // Convert \[ ... \] (may span multiple lines) to $$ ... $$
    text = text.replace(/\\\[([\s\S]*?)\\\]/g, '$$$$$1$$$$');
    // Convert standalone [ ... ] containing LaTeX commands to $$ ... $$
    // Only match when content contains backslash commands (to avoid false positives)
    text = text.replace(/(?:^|\n)\s*\[([\s\S]*?)\]\s*(?:\n|$)/g, function(match, inner) {
        if (/\\[a-zA-Z]/.test(inner)) {
            return '\n$$' + inner + '$$\n';
        }
        return match;
    });
    return text;
}

/**
 * Render markdown content with LaTeX math support.
 * Combines prepareMath → marked.parse → renderMath.
 * @param {string} content - Raw markdown/LaTeX text
 * @param {HTMLElement} element - Target element to render into
 */
function renderMarkdown(content, element) {
    if (!element) return;
    const processed = prepareMath(content);
    element.innerHTML = marked.parse(processed);
    renderMath(element);
}

/**
 * Render LaTeX math expressions in an element using KaTeX auto-render.
 * Call after inserting HTML from marked.parse() into the DOM.
 */
function renderMath(element) {
    if (typeof renderMathInElement === 'function' && element) {
        renderMathInElement(element, {
            delimiters: [
                { left: '$$', right: '$$', display: true },
                { left: '\\[', right: '\\]', display: true },
                { left: '\\(', right: '\\)', display: false },
                { left: '$', right: '$', display: false },
            ],
            throwOnError: false,
        });
    }
}
