(function(window, document) {
    'use strict';

    const HANDOFF_KEY = 'alphalab:portfolio-intent-handoff:v2';
    const LEGACY_HANDOFF_KEY = 'alphalab:portfolio-intent-handoff:v1';
    const HANDOFF_VERSION = 2;
    const HANDOFF_MAX_AGE_MS = 30 * 60 * 1000;
    const HANDOFF_FUTURE_TOLERANCE_MS = 5 * 60 * 1000;
    const HANDOFF_FIELDS = Object.freeze({
        risk_tolerance: Object.freeze({selector: '[name="risk_tolerance"]', maxLength: 80}),
        target_market: Object.freeze({selector: '[name="target_market"]', maxLength: 120}),
        investment_horizon: Object.freeze({selector: '[name="investment_horizon"]', maxLength: 120}),
        budget: Object.freeze({selector: '[name="budget"]', maxLength: 80})
    });
    const DESTINATIONS = Object.freeze({
        build: Object.freeze({path: '/build-portfolio', maxLength: 2500}),
        review: Object.freeze({path: '/review-portfolio', maxLength: 2500})
    });

    const STORAGE_FEATURE = 'draft-portfolio-intent';
    const STORAGE_SCHEMA_VERSION = 2;
    const DRAFT_FIELD_LIMITS = Object.freeze({
        market_country: 120,
        portfolio_type: 32,
        allocation_split: 80,
        budget: 80,
        risk_tolerance: 32,
        holding_horizon: 120,
        instrument_preference: 160,
        price_preference: 120,
        sector_stock_type_focus: 300,
        payout_frequency_preference: 32,
        excluded_risks_advice_categories: 1000,
        market_specific_mechanics: 1000,
        additional_context: 1500
    });
    const DRAFT_FIELD_NAMES = Object.freeze(Object.keys(DRAFT_FIELD_LIMITS));
    const VALID_PORTFOLIO_TYPES = Object.freeze([
        '', 'swing_trade', 'long_term_growth', 'long_term_income', 'balanced', 'custom'
    ]);
    const VALID_RISK_TOLERANCES = Object.freeze([
        '', 'conservative', 'moderate', 'aggressive', 'very_aggressive', 'custom'
    ]);
    const VALID_PAYOUT_FREQUENCIES = Object.freeze([
        '', 'monthly', 'quarterly', 'semi_annual', 'annual', 'accumulating'
    ]);
    const RISK_LABELS = Object.freeze({
        conservative: 'Conservative',
        moderate: 'Moderate',
        aggressive: 'Aggressive',
        very_aggressive: 'Very Aggressive',
        custom: 'Custom'
    });

    function safeSelector(selector) {
        if (!selector) return null;
        try {
            return document.querySelector(selector);
        } catch (error) {
            return null;
        }
    }

    function showHandoffStatus(field, message, isError) {
        const status = safeSelector(field.dataset.portfolioIntentHandoffStatus);
        if (!status) return;
        status.textContent = message;
        status.classList.toggle('alert-success', !isError);
        status.classList.toggle('alert-warning', isError);
        status.classList.remove('d-none');
    }

    function removeHandoff() {
        try {
            window.sessionStorage.removeItem(HANDOFF_KEY);
        } catch (error) {
            return false;
        }
        return true;
    }

    function removeLegacyHandoff() {
        try {
            window.sessionStorage.removeItem(LEGACY_HANDOFF_KEY);
        } catch (error) {
            return false;
        }
        return true;
    }

    function validHandoffFields(value) {
        if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
        return Object.keys(value).every(function(key) {
            return Object.prototype.hasOwnProperty.call(HANDOFF_FIELDS, key)
                && typeof value[key] === 'string'
                && value[key].length <= HANDOFF_FIELDS[key].maxLength;
        });
    }

    function isValidHandoff(value) {
        if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
        const keys = Object.keys(value);
        if (keys.length !== 5
                || !keys.every(function(key) {
                    return ['version', 'target', 'intent', 'fields', 'createdAt'].includes(key);
                })
                || value.version !== HANDOFF_VERSION
                || !Object.prototype.hasOwnProperty.call(DESTINATIONS, value.target)
                || typeof value.intent !== 'string'
                || !value.intent.trim()
                || value.intent.length > 2500
                || !validHandoffFields(value.fields)
                || typeof value.createdAt !== 'string') {
            return false;
        }
        const createdAt = Date.parse(value.createdAt);
        if (!Number.isFinite(createdAt)) return false;
        const age = Date.now() - createdAt;
        return age >= -HANDOFF_FUTURE_TOLERANCE_MS && age <= HANDOFF_MAX_AGE_MS;
    }

    function setFieldValue(field, proposedValue, conflicts) {
        if (!field || !proposedValue) return;
        let resolvedValue = proposedValue;
        if (field.tagName === 'SELECT') {
            const normalized = proposedValue.trim().toLowerCase();
            const option = Array.from(field.options).find(function(item) {
                return item.value.trim().toLowerCase() === normalized
                    || item.textContent.trim().toLowerCase() === normalized;
            });
            if (!option) {
                conflicts.push(field.name || field.id);
                return;
            }
            resolvedValue = option.value;
        }

        if (field.value.trim() && field.value.trim() !== resolvedValue.trim()) {
            conflicts.push(field.name || field.id);
            return;
        }
        if (field.value !== resolvedValue) {
            field.value = resolvedValue;
            field.dispatchEvent(new Event('input', {bubbles: true}));
            field.dispatchEvent(new Event('change', {bubbles: true}));
        }
    }

    function consumeMatchingHandoff() {
        const intentField = document.querySelector('[data-portfolio-intent-handoff-target]');
        if (!intentField || !['INPUT', 'TEXTAREA'].includes(intentField.tagName)) return;
        const expectedTarget = intentField.dataset.portfolioIntentHandoffTarget;
        if (!Object.prototype.hasOwnProperty.call(DESTINATIONS, expectedTarget)) return;

        let raw;
        try {
            raw = window.sessionStorage.getItem(HANDOFF_KEY);
        } catch (error) {
            showHandoffStatus(intentField, 'The drafted intent could not be read from this browser session.', true);
            return;
        }
        if (raw === null) return;

        let handoff;
        try {
            handoff = JSON.parse(raw);
        } catch (error) {
            removeHandoff();
            showHandoffStatus(intentField, 'The saved intent handoff was invalid and has been removed.', true);
            return;
        }
        if (!isValidHandoff(handoff)) {
            removeHandoff();
            showHandoffStatus(intentField, 'The saved intent handoff expired or was invalid and has been removed.', true);
            return;
        }
        if (handoff.target !== expectedTarget) return;

        const fieldLimit = intentField.maxLength > 0
            ? intentField.maxLength
            : DESTINATIONS[expectedTarget].maxLength;
        if (handoff.intent.length > fieldLimit) {
            removeHandoff();
            showHandoffStatus(intentField, `The drafted intent exceeds this field's ${fieldLimit}-character limit.`, true);
            return;
        }

        const conflicts = [];
        if (intentField.value.trim() && intentField.value.trim() !== handoff.intent.trim()) {
            const replace = typeof window.confirm === 'function'
                && window.confirm('Replace the existing portfolio intent with the newly drafted intent?');
            if (!replace) {
                conflicts.push(intentField.name || intentField.id);
            } else {
                intentField.value = handoff.intent;
            }
        } else {
            intentField.value = handoff.intent;
        }
        if (!conflicts.includes(intentField.name || intentField.id)) {
            intentField.dispatchEvent(new Event('input', {bubbles: true}));
            intentField.dispatchEvent(new Event('change', {bubbles: true}));
        }

        Object.keys(handoff.fields).forEach(function(name) {
            const definition = HANDOFF_FIELDS[name];
            setFieldValue(safeSelector(definition.selector), handoff.fields[name], conflicts);
        });
        removeHandoff();

        if (conflicts.length) {
            showHandoffStatus(
                intentField,
                `Draft processed, but existing values were kept for: ${conflicts.join(', ')}. Review them before continuing.`,
                true
            );
        } else {
            showHandoffStatus(intentField, 'Drafted intent and compatible preferences added. Review before continuing.', false);
        }
        intentField.focus({preventScroll: true});
        intentField.scrollIntoView({block: 'center'});
    }

    function defaultInputs() {
        const inputs = {};
        DRAFT_FIELD_NAMES.forEach(function(name) {
            inputs[name] = '';
        });
        return inputs;
    }

    function defaultStoredDraft() {
        return {
            inputs: defaultInputs(),
            status: 'idle',
            intent: '',
            assumptions: [],
            questions: [],
            clarificationAnswers: {},
            followupAttempted: false
        };
    }

    function validQuestion(value) {
        return value !== null
            && typeof value === 'object'
            && !Array.isArray(value)
            && Object.keys(value).length === 2
            && typeof value.id === 'string'
            && /^[a-z][a-z0-9_]{0,39}$/.test(value.id)
            && typeof value.question === 'string'
            && value.question.trim().length > 0
            && value.question.length <= 300;
    }

    function validStoredDraft(value) {
        if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
        const keys = Object.keys(value);
        if (keys.length !== 7
                || !keys.every(function(key) {
                    return [
                        'inputs', 'status', 'intent', 'assumptions', 'questions',
                        'clarificationAnswers', 'followupAttempted'
                    ].includes(key);
                })
                || value.inputs === null
                || typeof value.inputs !== 'object'
                || Array.isArray(value.inputs)
                || Object.keys(value.inputs).length !== DRAFT_FIELD_NAMES.length
                || !DRAFT_FIELD_NAMES.every(function(name) {
                    return Object.prototype.hasOwnProperty.call(value.inputs, name)
                        && typeof value.inputs[name] === 'string'
                        && value.inputs[name].length <= DRAFT_FIELD_LIMITS[name];
                })
                || !VALID_PORTFOLIO_TYPES.includes(value.inputs.portfolio_type)
                || !VALID_RISK_TOLERANCES.includes(value.inputs.risk_tolerance)
                || !VALID_PAYOUT_FREQUENCIES.includes(value.inputs.payout_frequency_preference)
                || !['idle', 'needs_clarification', 'complete'].includes(value.status)
                || typeof value.intent !== 'string'
                || value.intent.length > 2500
                || !Array.isArray(value.assumptions)
                || value.assumptions.length > 5
                || !value.assumptions.every(function(item) {
                    return typeof item === 'string' && item.trim().length > 0 && item.length <= 300;
                })
                || !Array.isArray(value.questions)
                || value.questions.length > 3
                || !value.questions.every(validQuestion)
                || value.clarificationAnswers === null
                || typeof value.clarificationAnswers !== 'object'
                || Array.isArray(value.clarificationAnswers)
                || typeof value.followupAttempted !== 'boolean') {
            return false;
        }
        const questionIds = value.questions.map(function(question) {
            return question.id;
        });
        const answerKeys = Object.keys(value.clarificationAnswers);
        if (new Set(questionIds).size !== questionIds.length
                || answerKeys.some(function(key) {
                    return !questionIds.includes(key)
                        || typeof value.clarificationAnswers[key] !== 'string'
                        || value.clarificationAnswers[key].length > 600;
                })) {
            return false;
        }
        if (value.status === 'complete') {
            return Boolean(value.intent.trim()) && value.questions.length === 0;
        }
        if (value.status === 'needs_clarification') {
            return !value.intent && value.questions.length > 0 && value.assumptions.length === 0;
        }
        return !value.intent && value.questions.length === 0 && value.assumptions.length === 0;
    }

    function showStorageWarning(message) {
        const warning = document.getElementById('draft-portfolio-intent-storage-warning');
        const text = document.getElementById('draft-portfolio-intent-storage-warning-message');
        if (!warning || !text) return;
        text.textContent = message;
        warning.classList.remove('d-none');
    }

    window.addEventListener('alphalab:storage-warning', function(event) {
        const detail = event.detail;
        if (!detail || (detail.feature !== STORAGE_FEATURE && detail.feature !== null)) return;
        const resetCodes = ['corrupt-value', 'validation-failed', 'migration-failed', 'schema-mismatch'];
        showStorageWarning(
            resetCodes.includes(detail.code)
                ? 'The saved portfolio-intent draft was invalid and has been reset.'
                : 'Local storage is unavailable. This draft will remain only for the current page.'
        );
    });

    function initDrafter() {
        const form = document.getElementById('draft-portfolio-intent-form');
        if (!form) return;

        const endpoint = '/portfolio-intent/draft';
        const errorElement = document.getElementById('draft-portfolio-intent-error');
        const clarificationSection = document.getElementById('draft-portfolio-intent-clarifications');
        const questionsElement = document.getElementById('draft-portfolio-intent-questions');
        const submitButton = document.getElementById('draft-portfolio-intent-submit');
        const submitLabel = document.getElementById('draft-portfolio-intent-submit-label');
        const spinner = document.getElementById('draft-portfolio-intent-spinner');
        const submitIcon = document.getElementById('draft-portfolio-intent-submit-icon');
        const resultSection = document.getElementById('draft-portfolio-intent-result-section');
        const resultTextarea = document.getElementById('draft-portfolio-intent-result');
        const assumptionsSection = document.getElementById('draft-portfolio-intent-assumptions-section');
        const assumptionsList = document.getElementById('draft-portfolio-intent-assumptions');
        const restoredNotice = document.getElementById('draft-portfolio-intent-restored');
        const handoffButtons = document.querySelectorAll('[data-portfolio-intent-destination]');

        let activeRequest = null;
        let activeQuestions = [];
        let draftStatus = 'idle';
        let assumptions = [];
        let followupAttempted = false;
        let restoring = true;

        function setLoading(loading) {
            submitButton.disabled = loading;
            spinner.classList.toggle('d-none', !loading);
            submitIcon.classList.toggle('d-none', loading);
            form.setAttribute('aria-busy', String(loading));
            handoffButtons.forEach(function(button) {
                button.disabled = loading;
            });
            submitLabel.textContent = loading
                ? (activeQuestions.length ? 'Submitting answers...' : 'Drafting...')
                : (activeQuestions.length ? 'Submit clarifications' : 'Draft intent');
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

        function collectInputs() {
            const inputs = {};
            DRAFT_FIELD_NAMES.forEach(function(name) {
                const field = form.elements.namedItem(name);
                inputs[name] = field && typeof field.value === 'string' ? field.value.trim() : '';
            });
            return inputs;
        }

        function collectAnswers() {
            const answers = {};
            questionsElement.querySelectorAll('[data-clarification-id]').forEach(function(input) {
                const answer = input.value.trim();
                if (answer) answers[input.dataset.clarificationId] = answer;
            });
            return answers;
        }

        function saveState() {
            const state = {
                inputs: collectInputs(),
                status: draftStatus,
                intent: draftStatus === 'complete' ? resultTextarea.value : '',
                assumptions: draftStatus === 'complete' ? assumptions : [],
                questions: draftStatus === 'needs_clarification' ? activeQuestions : [],
                clarificationAnswers: draftStatus === 'needs_clarification' ? collectAnswers() : {},
                followupAttempted: followupAttempted
            };
            try {
                const persisted = window.AlphaLabStorage.save(STORAGE_FEATURE, state, {
                    schemaVersion: STORAGE_SCHEMA_VERSION,
                    validate: validStoredDraft
                });
                if (persisted === false) {
                    showStorageWarning('This draft is available only for the current page and could not be persisted.');
                }
            } catch (error) {
                showStorageWarning('This draft could not be saved. You can continue on the current page.');
            }
        }

        function renderAssumptions(values) {
            const fragment = document.createDocumentFragment();
            values.forEach(function(value) {
                const item = document.createElement('li');
                item.textContent = value;
                fragment.appendChild(item);
            });
            assumptionsList.replaceChildren(fragment);
            assumptionsSection.classList.toggle('d-none', values.length === 0);
        }

        function renderClarifications(questions, answers, focusFirst) {
            const fragment = document.createDocumentFragment();
            questions.forEach(function(item, index) {
                const column = document.createElement('div');
                column.className = 'col-12';
                const label = document.createElement('label');
                const inputId = `draft-portfolio-clarification-${index + 1}`;
                label.className = 'form-label fw-semibold';
                label.htmlFor = inputId;
                label.textContent = item.question;

                const required = document.createElement('span');
                required.className = 'text-danger ms-1';
                required.setAttribute('aria-hidden', 'true');
                required.textContent = '*';
                label.appendChild(required);

                const input = document.createElement('textarea');
                input.className = 'form-control';
                input.id = inputId;
                input.rows = 3;
                input.maxLength = 600;
                input.required = true;
                input.dataset.clarificationId = item.id;
                input.value = answers[item.id] || '';
                column.appendChild(label);
                column.appendChild(input);
                fragment.appendChild(column);
            });
            activeQuestions = questions.map(function(item) {
                return {id: item.id, question: item.question};
            });
            draftStatus = 'needs_clarification';
            questionsElement.replaceChildren(fragment);
            clarificationSection.classList.remove('d-none');
            resultSection.classList.add('d-none');
            submitLabel.textContent = 'Submit clarifications';
            if (focusFirst) {
                const firstInput = questionsElement.querySelector('textarea');
                if (firstInput) firstInput.focus();
            }
            saveState();
        }

        function clearGeneratedState() {
            activeQuestions = [];
            assumptions = [];
            followupAttempted = false;
            draftStatus = 'idle';
            questionsElement.replaceChildren();
            clarificationSection.classList.add('d-none');
            resultTextarea.value = '';
            renderAssumptions([]);
            resultSection.classList.add('d-none');
            restoredNotice.classList.add('d-none');
        }

        function showCompletedIntent(intent, responseAssumptions, restored) {
            activeQuestions = [];
            assumptions = responseAssumptions.slice();
            draftStatus = 'complete';
            questionsElement.replaceChildren();
            clarificationSection.classList.add('d-none');
            resultTextarea.value = intent.trim();
            renderAssumptions(assumptions);
            resultSection.classList.remove('d-none');
            restoredNotice.classList.toggle('d-none', !restored);
            if (!restored) {
                resultSection.scrollIntoView({block: 'start'});
                resultTextarea.focus({preventScroll: true});
            }
            saveState();
        }

        function normalizedQuestions(data) {
            if (!data || data.status !== 'needs_clarification' || data.intent !== null
                    || !Array.isArray(data.questions) || data.questions.length === 0
                    || data.questions.length > 3
                    || !Array.isArray(data.assumptions) || data.assumptions.length !== 0) {
                return null;
            }
            const seen = new Set();
            const questions = [];
            for (const item of data.questions) {
                if (!validQuestion(item) || seen.has(item.id)) return null;
                seen.add(item.id);
                questions.push({id: item.id, question: item.question.trim()});
            }
            return questions;
        }

        function normalizedAssumptions(data) {
            if (!Array.isArray(data.assumptions) || data.assumptions.length > 5) return null;
            const values = [];
            for (const value of data.assumptions) {
                if (typeof value !== 'string' || !value.trim() || value.length > 300) return null;
                values.push(value.trim());
            }
            return values;
        }

        function fastApiErrorMessage(data, responseStatus) {
            if (data && typeof data.detail === 'string' && data.detail.trim()) return data.detail;
            if (data && Array.isArray(data.detail)) {
                const messages = data.detail.map(function(item) {
                    if (!item || typeof item.msg !== 'string') return '';
                    const location = Array.isArray(item.loc)
                        ? item.loc.filter(function(part) {
                            return part !== 'body';
                        }).join('.')
                        : '';
                    return location ? `${location}: ${item.msg}` : item.msg;
                }).filter(Boolean);
                if (messages.length) return messages.join(' ');
            }
            return `The server could not draft the intent (HTTP ${responseStatus}). Please try again.`;
        }

        function buildHandoffFields() {
            const inputs = collectInputs();
            const fields = {};
            if (RISK_LABELS[inputs.risk_tolerance]) fields.risk_tolerance = RISK_LABELS[inputs.risk_tolerance];
            if (inputs.market_country) fields.target_market = inputs.market_country;
            if (inputs.holding_horizon) fields.investment_horizon = inputs.holding_horizon;
            if (inputs.budget) fields.budget = inputs.budget;
            return fields;
        }

        function storeHandoff(target) {
            clearError();
            const destination = DESTINATIONS[target];
            const intent = resultTextarea.value;
            if (!destination || !intent.trim() || draftStatus !== 'complete') {
                showError('Generate or restore a current portfolio intent before continuing.');
                return;
            }
            if (intent.length > destination.maxLength) {
                showError(`Shorten the intent to ${destination.maxLength} characters or fewer before continuing.`);
                return;
            }
            const handoff = {
                version: HANDOFF_VERSION,
                target: target,
                intent: intent,
                fields: buildHandoffFields(),
                createdAt: new Date().toISOString()
            };
            try {
                window.sessionStorage.setItem(HANDOFF_KEY, JSON.stringify(handoff));
            } catch (error) {
                showError('This browser session could not store the intent. Check storage settings and try again.');
                return;
            }
            saveState();
            window.location.assign(destination.path);
        }

        let stored = defaultStoredDraft();
        try {
            stored = window.AlphaLabStorage.load(STORAGE_FEATURE, {
                schemaVersion: STORAGE_SCHEMA_VERSION,
                defaultValue: defaultStoredDraft(),
                validate: validStoredDraft
            });
        } catch (error) {
            showStorageWarning('The saved portfolio-intent draft could not be restored.');
        }
        if (validStoredDraft(stored)) {
            DRAFT_FIELD_NAMES.forEach(function(name) {
                const field = form.elements.namedItem(name);
                if (field) field.value = stored.inputs[name];
            });
            followupAttempted = stored.followupAttempted;
            if (stored.status === 'needs_clarification') {
                renderClarifications(stored.questions, stored.clarificationAnswers, false);
            } else if (stored.status === 'complete') {
                showCompletedIntent(stored.intent, stored.assumptions, true);
            }
        }
        restoring = false;

        form.addEventListener('input', function(event) {
            clearError();
            if (event.target.matches('[data-draft-field]')) {
                if (!restoring && draftStatus !== 'idle') clearGeneratedState();
                saveState();
                return;
            }
            if (event.target.matches('[data-clarification-id]')) {
                event.target.setCustomValidity('');
                saveState();
            }
        });

        resultTextarea.addEventListener('input', function() {
            clearError();
            restoredNotice.classList.add('d-none');
            if (draftStatus === 'complete') saveState();
        });
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
                input.setCustomValidity(input.value.trim() ? '' : 'Please answer this clarification question.');
            });
            if (!form.checkValidity()) {
                form.reportValidity();
                return;
            }
            if (activeQuestions.length && followupAttempted) {
                showError('The clarification follow-up has already been attempted. Update your preferences to start again.');
                return;
            }

            const inputs = collectInputs();
            const requestBody = {};
            Object.keys(inputs).forEach(function(name) {
                if (inputs[name]) requestBody[name] = inputs[name];
            });
            if (activeQuestions.length) {
                requestBody.clarification_round = 1;
                requestBody.prior_questions = activeQuestions;
                requestBody.clarifications = collectAnswers();
                followupAttempted = true;
                saveState();
            } else {
                requestBody.clarification_round = 0;
            }

            if (activeRequest) activeRequest.abort();
            const controller = new AbortController();
            activeRequest = controller;
            setLoading(true);

            try {
                const response = await fetch(endpoint, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {'Accept': 'application/json', 'Content-Type': 'application/json'},
                    body: JSON.stringify(requestBody),
                    signal: controller.signal
                });
                let data = null;
                try {
                    data = await response.json();
                } catch (parseError) {
                    showError('The server returned an unreadable response. Please try again.');
                    return;
                }
                if (!response.ok) {
                    showError(fastApiErrorMessage(data, response.status));
                    return;
                }

                if (data && data.status === 'complete'
                        && typeof data.intent === 'string' && data.intent.trim()
                        && data.intent.length <= 2500
                        && Array.isArray(data.questions) && data.questions.length === 0) {
                    const responseAssumptions = normalizedAssumptions(data);
                    if (responseAssumptions === null) {
                        showError('The server returned invalid draft assumptions. Please try again.');
                        return;
                    }
                    showCompletedIntent(data.intent, responseAssumptions, false);
                    form.classList.remove('was-validated');
                    return;
                }

                const questions = normalizedQuestions(data);
                if (questions && !activeQuestions.length) {
                    followupAttempted = false;
                    renderClarifications(questions, {}, true);
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
            saveState();
            if (activeRequest) {
                activeRequest.abort();
                activeRequest = null;
            }
        });
    }

    function initialize() {
        removeLegacyHandoff();
        consumeMatchingHandoff();
        initDrafter();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize);
    } else {
        initialize();
    }
}(window, document));
