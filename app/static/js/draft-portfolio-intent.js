(function(window, document) {
    'use strict';

    /*
     * One-time cross-page handoff. This is intentionally the only key used:
     *   alphalab:portfolio-intent-handoff:v1
     * Schema:
     *   {version: 1, target: "build"|"review", intent: string, createdAt: ISO-8601 string}
     *
     * The payload is session-scoped, is never placed in a URL or persistent storage,
     * and is removed only after the matching destination field accepts it.
     */
    const HANDOFF_KEY = 'alphalab:portfolio-intent-handoff:v1';
    const HANDOFF_VERSION = 1;
    const DESTINATIONS = Object.freeze({
        build: Object.freeze({path: '/build-portfolio', maxLength: 2500}),
        review: Object.freeze({path: '/review-portfolio', maxLength: 2500})
    });

    function safeSelector(selector) {
        if (!selector) {
            return null;
        }
        try {
            return document.querySelector(selector);
        } catch (error) {
            return null;
        }
    }

    function showHandoffStatus(field, message, isError) {
        const status = safeSelector(field.dataset.portfolioIntentHandoffStatus);
        if (!status) {
            return;
        }
        status.textContent = message;
        status.classList.toggle('alert-success', !isError);
        status.classList.toggle('alert-warning', isError);
        status.classList.remove('d-none');
    }

    function isValidHandoff(value) {
        if (value === null || typeof value !== 'object' || Array.isArray(value)) {
            return false;
        }
        const keys = Object.keys(value);
        return keys.length === 4
            && keys.every(function(key) {
                return ['version', 'target', 'intent', 'createdAt'].includes(key);
            })
            && value.version === HANDOFF_VERSION
            && Object.prototype.hasOwnProperty.call(DESTINATIONS, value.target)
            && typeof value.intent === 'string'
            && value.intent.trim().length > 0
            && value.intent.length <= 2500
            && typeof value.createdAt === 'string'
            && Number.isFinite(Date.parse(value.createdAt));
    }

    function consumeMatchingHandoff() {
        const field = document.querySelector('[data-portfolio-intent-handoff-target]');
        if (!field || !['INPUT', 'TEXTAREA'].includes(field.tagName)) {
            return;
        }

        const expectedTarget = field.dataset.portfolioIntentHandoffTarget;
        if (!Object.prototype.hasOwnProperty.call(DESTINATIONS, expectedTarget)) {
            return;
        }

        let raw;
        try {
            raw = window.sessionStorage.getItem(HANDOFF_KEY);
        } catch (error) {
            showHandoffStatus(
                field,
                'The drafted intent could not be read from this browser session.',
                true
            );
            return;
        }
        if (raw === null) {
            return;
        }

        let handoff;
        try {
            handoff = JSON.parse(raw);
        } catch (error) {
            return;
        }
        if (!isValidHandoff(handoff) || handoff.target !== expectedTarget) {
            return;
        }

        const fieldLimit = field.maxLength > 0 ? field.maxLength : DESTINATIONS[expectedTarget].maxLength;
        if (handoff.intent.length > fieldLimit) {
            showHandoffStatus(
                field,
                `The drafted intent is longer than this field's ${fieldLimit.toLocaleString()}-character limit. `
                    + 'Return to the drafter and shorten it.',
                true
            );
            return;
        }

        field.value = handoff.intent;
        field.dispatchEvent(new Event('input', {bubbles: true}));
        field.dispatchEvent(new Event('change', {bubbles: true}));

        try {
            window.sessionStorage.removeItem(HANDOFF_KEY);
        } catch (error) {
            showHandoffStatus(
                field,
                'The intent was added, but its one-time session handoff could not be cleared.',
                true
            );
            return;
        }

        showHandoffStatus(
            field,
            'Drafted intent added. Review or edit it before continuing.',
            false
        );
        field.focus({preventScroll: true});
        field.scrollIntoView({block: 'center'});
    }

    function initDrafter() {
        const form = document.getElementById('draft-portfolio-intent-form');
        if (!form) {
            return;
        }

        const endpoint = '/portfolio-intent/draft';
        const errorElement = document.getElementById('draft-portfolio-intent-error');
        const clarificationSection = document.getElementById(
            'draft-portfolio-intent-clarifications'
        );
        const questionsElement = document.getElementById('draft-portfolio-intent-questions');
        const submitButton = document.getElementById('draft-portfolio-intent-submit');
        const submitLabel = document.getElementById('draft-portfolio-intent-submit-label');
        const spinner = document.getElementById('draft-portfolio-intent-spinner');
        const submitIcon = document.getElementById('draft-portfolio-intent-submit-icon');
        const resultSection = document.getElementById(
            'draft-portfolio-intent-result-section'
        );
        const resultTextarea = document.getElementById('draft-portfolio-intent-result');
        const handoffButtons = document.querySelectorAll(
            '[data-portfolio-intent-destination]'
        );
        const clarificationAnswers = new Map();
        let activeRequest = null;

        function setLoading(loading) {
            submitButton.disabled = loading;
            spinner.classList.toggle('d-none', !loading);
            submitIcon.classList.toggle('d-none', loading);
            form.setAttribute('aria-busy', String(loading));
            handoffButtons.forEach(function(button) {
                button.disabled = loading;
            });
            if (loading) {
                submitLabel.textContent = clarificationSection.classList.contains('d-none')
                    ? 'Drafting…'
                    : 'Submitting answers…';
            } else {
                submitLabel.textContent = clarificationSection.classList.contains('d-none')
                    ? 'Draft intent'
                    : 'Submit clarifications';
            }
        }

        function clearError() {
            errorElement.textContent = '';
            errorElement.classList.add('d-none');
        }

        function showError(message) {
            errorElement.textContent = message;
            errorElement.classList.remove('d-none');
            errorElement.focus();
        }

        function collectDraftFields() {
            const payload = {};
            form.querySelectorAll('[data-draft-field]').forEach(function(field) {
                const value = field.value.trim();
                if (value) {
                    payload[field.name] = value;
                }
            });
            return payload;
        }

        function rememberClarificationAnswers() {
            questionsElement.querySelectorAll('[data-clarification-id]').forEach(function(input) {
                clarificationAnswers.set(input.dataset.clarificationId, input.value.trim());
            });
        }

        function currentClarifications() {
            const clarifications = Object.create(null);
            questionsElement.querySelectorAll('[data-clarification-id]').forEach(function(input) {
                const answer = input.value.trim();
                if (answer) {
                    clarifications[input.dataset.clarificationId] = answer;
                }
            });
            return Object.keys(clarifications).length ? clarifications : null;
        }

        function fastApiErrorMessage(data, responseStatus) {
            if (data && typeof data.detail === 'string' && data.detail.trim()) {
                return data.detail;
            }
            if (data && Array.isArray(data.detail)) {
                const messages = data.detail.map(function(item) {
                    if (!item || typeof item.msg !== 'string') {
                        return '';
                    }
                    const location = Array.isArray(item.loc)
                        ? item.loc.filter(function(part) {
                            return part !== 'body';
                        }).join('.')
                        : '';
                    return location ? `${location}: ${item.msg}` : item.msg;
                }).filter(Boolean);
                if (messages.length) {
                    return messages.join(' ');
                }
            }
            if (data && typeof data.message === 'string' && data.message.trim()) {
                return data.message;
            }
            if (data && typeof data.error === 'string' && data.error.trim()) {
                return data.error;
            }
            return `The server could not draft the intent (HTTP ${responseStatus}). Please try again.`;
        }

        function normalizedQuestions(data) {
            if (!data || data.status !== 'needs_clarification' || data.intent !== null
                    || !Array.isArray(data.questions) || data.questions.length === 0
                    || data.questions.length > 3) {
                return null;
            }
            const seenIds = new Set();
            const questions = [];
            for (const item of data.questions) {
                if (!item || typeof item.id !== 'string'
                        || !/^[a-z][a-z0-9_]{0,39}$/.test(item.id)
                        || typeof item.question !== 'string'
                        || !item.question.trim() || item.question.length > 300
                        || seenIds.has(item.id)) {
                    return null;
                }
                seenIds.add(item.id);
                questions.push({id: item.id, question: item.question.trim()});
            }
            return questions;
        }

        function renderClarifications(questions) {
            const fragment = document.createDocumentFragment();
            questions.forEach(function(item, index) {
                const column = document.createElement('div');
                column.className = 'col-12';

                const label = document.createElement('label');
                const inputId = `draft-portfolio-clarification-${index + 1}`;
                label.className = 'form-label fw-semibold';
                label.htmlFor = inputId;
                label.textContent = item.question;

                const requiredText = document.createElement('span');
                requiredText.className = 'text-danger ms-1';
                requiredText.setAttribute('aria-hidden', 'true');
                requiredText.textContent = '*';
                label.appendChild(requiredText);

                const accessibleRequiredText = document.createElement('span');
                accessibleRequiredText.className = 'visually-hidden';
                accessibleRequiredText.textContent = ' (required)';
                label.appendChild(accessibleRequiredText);

                const input = document.createElement('textarea');
                input.className = 'form-control';
                input.id = inputId;
                input.rows = 3;
                input.maxLength = 600;
                input.required = true;
                input.setAttribute('aria-required', 'true');
                input.dataset.clarificationId = item.id;
                input.value = clarificationAnswers.get(item.id) || '';

                column.appendChild(label);
                column.appendChild(input);
                fragment.appendChild(column);
            });

            questionsElement.replaceChildren(fragment);
            clarificationSection.classList.remove('d-none');
            submitLabel.textContent = 'Submit clarifications';
            const firstInput = questionsElement.querySelector('textarea');
            if (firstInput) {
                firstInput.focus();
            }
        }

        function clearClarifications() {
            questionsElement.replaceChildren();
            clarificationSection.classList.add('d-none');
            clarificationAnswers.clear();
            submitLabel.textContent = 'Draft intent';
        }

        function showCompletedIntent(intent) {
            resultTextarea.value = intent.trim();
            resultSection.classList.remove('d-none');
            resultSection.scrollIntoView({block: 'start'});
            resultTextarea.focus({preventScroll: true});
        }

        function storeHandoff(target) {
            clearError();
            const destination = DESTINATIONS[target];
            const intent = resultTextarea.value;
            if (!destination || !intent.trim()) {
                showError('Enter or generate a portfolio intent before continuing.');
                resultTextarea.focus();
                return;
            }
            if (intent.length > destination.maxLength) {
                showError(
                    `Shorten the intent to ${destination.maxLength.toLocaleString()} characters `
                        + `or fewer before using it in ${target === 'build' ? 'Build' : 'Review'} Portfolio.`
                );
                resultTextarea.focus();
                return;
            }

            const handoff = {
                version: HANDOFF_VERSION,
                target: target,
                intent: intent,
                createdAt: new Date().toISOString()
            };
            try {
                window.sessionStorage.setItem(HANDOFF_KEY, JSON.stringify(handoff));
            } catch (error) {
                showError(
                    'This browser session could not store the intent. Check browser storage settings and try again.'
                );
                return;
            }
            window.location.assign(destination.path);
        }

        form.addEventListener('input', function(event) {
            clearError();
            if (event.target.matches('[data-clarification-id]')) {
                event.target.setCustomValidity('');
            }
        });

        resultTextarea.addEventListener('input', clearError);
        handoffButtons.forEach(function(button) {
            button.addEventListener('click', function() {
                storeHandoff(button.dataset.portfolioIntentDestination);
            });
        });

        form.addEventListener('submit', async function(event) {
            event.preventDefault();
            clearError();
            form.classList.add('was-validated');
            questionsElement.querySelectorAll('[data-clarification-id]').forEach(function(input) {
                input.setCustomValidity(
                    input.value.trim() ? '' : 'Please answer this clarification question.'
                );
            });
            if (!form.checkValidity()) {
                form.reportValidity();
                return;
            }

            rememberClarificationAnswers();
            const requestBody = collectDraftFields();
            const clarifications = currentClarifications();
            if (clarifications) {
                requestBody.clarifications = clarifications;
            }

            if (activeRequest) {
                activeRequest.abort();
            }
            const controller = new AbortController();
            activeRequest = controller;
            setLoading(true);

            try {
                const response = await fetch(endpoint, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(requestBody),
                    signal: controller.signal
                });

                let data = null;
                try {
                    data = await response.json();
                } catch (parseError) {
                    showError(response.ok
                        ? 'The server returned an unreadable response. Please try again.'
                        : fastApiErrorMessage(null, response.status));
                    return;
                }

                if (!response.ok) {
                    showError(fastApiErrorMessage(data, response.status));
                    return;
                }

                if (data && data.status === 'complete') {
                    if (typeof data.intent !== 'string' || !data.intent.trim()
                            || data.intent.length > 2500
                            || !Array.isArray(data.questions) || data.questions.length !== 0) {
                        showError('The server returned an incomplete intent. Please try again.');
                        return;
                    }
                    clearClarifications();
                    form.classList.remove('was-validated');
                    showCompletedIntent(data.intent);
                    return;
                }

                const questions = normalizedQuestions(data);
                if (questions) {
                    renderClarifications(questions);
                    form.classList.remove('was-validated');
                    return;
                }

                showError('The server returned an unexpected response. Please try again.');
            } catch (error) {
                if (error.name !== 'AbortError') {
                    showError('Unable to reach the server. Check your connection and try again.');
                }
            } finally {
                if (activeRequest === controller) {
                    activeRequest = null;
                    setLoading(false);
                }
            }
        });

        window.addEventListener('pagehide', function() {
            if (activeRequest) {
                activeRequest.abort();
                activeRequest = null;
            }
        });
    }

    function initialize() {
        /*
         * Destination templates register their persistent-form restore handlers before
         * this script. Consuming here lets the one-time intent deliberately win over
         * restored form data without writing the handoff to persistent storage.
         */
        consumeMatchingHandoff();
        initDrafter();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize);
    } else {
        initialize();
    }
}(window, document));
