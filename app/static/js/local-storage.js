/**
 * User-scoped, versioned browser storage for JSON-compatible feature data.
 *
 * AlphaLabStorage.load() and data returned from the in-memory fallback are
 * cloned where possible. Callers must pass JSON-compatible data/defaults and
 * synchronous validate/migrate callbacks.
 */
(function (window, document) {
    'use strict';

    const META_NAME = 'alphalab-storage-user-key';
    const USER_KEY_PATTERN = /^[A-Za-z0-9_-]{22}$/;
    const FEATURE_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
    const memory = new Map();
    const emittedGlobalWarnings = new Set();

    let storage = null;
    let persistent = false;

    const meta = document.querySelector(`meta[name="${META_NAME}"]`);
    const metaValue = meta ? meta.getAttribute('content') : null;
    const userKey = typeof metaValue === 'string' && USER_KEY_PATTERN.test(metaValue)
        ? metaValue
        : null;

    /**
     * Emit a non-visual storage warning.
     *
     * @param {string|null} feature Feature associated with the warning.
     * @param {string} code Stable machine-readable warning code.
     * @param {string} message Human-readable warning.
     * @param {boolean} [globalWarning=false] Whether to emit this code once/page.
     */
    function warn(feature, code, message, globalWarning = false) {
        if (globalWarning) {
            if (emittedGlobalWarnings.has(code)) {
                return;
            }
            emittedGlobalWarnings.add(code);
        }

        const detail = {feature, code, message};

        try {
            if (window.console && typeof window.console.warn === 'function') {
                window.console.warn(`[AlphaLabStorage:${code}] ${message}`, detail);
            }
        } catch (error) {
            // Warning reporting must never interfere with the page.
        }

        try {
            let event;
            if (typeof window.CustomEvent === 'function') {
                event = new window.CustomEvent('alphalab:storage-warning', {detail});
            } else {
                event = document.createEvent('CustomEvent');
                event.initCustomEvent('alphalab:storage-warning', false, false, detail);
            }
            window.dispatchEvent(event);
        } catch (error) {
            // Event reporting is best effort in older/restricted environments.
        }
    }

    function errorContext(error) {
        if (!error) {
            return '';
        }
        const name = typeof error.name === 'string' && error.name ? error.name : 'Error';
        const message = typeof error.message === 'string' && error.message
            ? `: ${error.message}`
            : '';
        return ` (${name}${message})`;
    }

    function markUnavailable(operation, error) {
        persistent = false;
        warn(
            null,
            'storage-unavailable',
            `localStorage is unavailable during ${operation}${errorContext(error)}; `
                + 'using in-memory storage for this page.',
            true,
        );
    }

    function assertFeature(feature) {
        if (typeof feature !== 'string' || !FEATURE_PATTERN.test(feature)) {
            throw new TypeError(
                'Feature must be 1-64 characters, begin with a lowercase letter or number, '
                    + 'and contain only lowercase letters, numbers, "_" or "-".',
            );
        }
        return feature;
    }

    function persistentKey(feature) {
        return `al:${userKey}:${feature}`;
    }

    function cloneJson(value, label, allowUndefined = false) {
        if (value === undefined && allowUndefined) {
            return undefined;
        }

        try {
            const serialized = JSON.stringify(value);
            if (serialized === undefined) {
                throw new TypeError('Value cannot be represented as JSON.');
            }
            return JSON.parse(serialized);
        } catch (error) {
            throw new TypeError(`${label} must be JSON-compatible.`);
        }
    }

    function normalizeOptions(options, allowMigration) {
        const normalized = options === undefined ? {} : options;
        if (
            normalized === null
            || typeof normalized !== 'object'
            || Array.isArray(normalized)
        ) {
            throw new TypeError('Options must be an object.');
        }

        const schemaVersion = normalized.schemaVersion === undefined
            ? 1
            : normalized.schemaVersion;
        if (!Number.isInteger(schemaVersion) || schemaVersion < 1) {
            throw new TypeError('schemaVersion must be a positive integer.');
        }

        if (
            normalized.validate !== undefined
            && typeof normalized.validate !== 'function'
        ) {
            throw new TypeError('validate must be a function.');
        }

        if (
            allowMigration
            && normalized.migrate !== undefined
            && typeof normalized.migrate !== 'function'
        ) {
            throw new TypeError('migrate must be a function.');
        }

        return {
            schemaVersion,
            defaultValue: normalized.defaultValue,
            validate: normalized.validate,
            migrate: allowMigration ? normalized.migrate : undefined,
        };
    }

    function validateData(data, validator) {
        if (!validator) {
            return {valid: true, error: null};
        }

        try {
            return {
                valid: validator(cloneJson(data, 'Feature data')) === true,
                error: null,
            };
        } catch (error) {
            return {valid: false, error};
        }
    }

    function validEnvelope(envelope) {
        return (
            envelope !== null
            && typeof envelope === 'object'
            && !Array.isArray(envelope)
            && Number.isInteger(envelope.schemaVersion)
            && envelope.schemaVersion > 0
            && typeof envelope.updatedAt === 'string'
            && Number.isFinite(Date.parse(envelope.updatedAt))
            && Object.prototype.hasOwnProperty.call(envelope, 'data')
        );
    }

    function safeGet(key) {
        if (!persistent || !storage) {
            return {succeeded: false, value: null};
        }

        try {
            return {succeeded: true, value: storage.getItem(key)};
        } catch (error) {
            markUnavailable('read', error);
            return {succeeded: false, value: null};
        }
    }

    function safeSet(key, value) {
        if (!persistent || !storage) {
            return false;
        }

        try {
            storage.setItem(key, value);
            return true;
        } catch (error) {
            markUnavailable('write', error);
            return false;
        }
    }

    function safeRemove(key) {
        if (!persistent || !storage) {
            return false;
        }

        try {
            storage.removeItem(key);
            return true;
        } catch (error) {
            markUnavailable('removal', error);
            return false;
        }
    }

    function discard(feature, source) {
        memory.delete(feature);
        if (source === 'persistent') {
            safeRemove(persistentKey(feature));
        }
    }

    function persistPrepared(feature, data, schemaVersion) {
        const envelope = {
            schemaVersion,
            updatedAt: new Date().toISOString(),
            data: cloneJson(data, 'Feature data'),
        };
        const serialized = JSON.stringify(envelope);

        if (userKey && safeSet(persistentKey(feature), serialized)) {
            memory.delete(feature);
            return true;
        }

        memory.set(feature, cloneJson(envelope, 'Storage envelope'));
        return false;
    }

    function probeStorage() {
        if (!userKey) {
            return;
        }

        let candidate;
        let probeKey;
        try {
            candidate = window.localStorage;
            if (!candidate) {
                throw new Error('localStorage is not present.');
            }

            const suffix = Math.random().toString(36).slice(2);
            probeKey = persistentKey(`storage-probe-${suffix}`);
            const probeValue = JSON.stringify({
                schemaVersion: 1,
                updatedAt: new Date().toISOString(),
                data: null,
            });

            candidate.setItem(probeKey, probeValue);
            if (candidate.getItem(probeKey) !== probeValue) {
                throw new Error('localStorage probe could not be read back.');
            }
            candidate.removeItem(probeKey);

            storage = candidate;
            persistent = true;
        } catch (error) {
            if (candidate && probeKey) {
                try {
                    candidate.removeItem(probeKey);
                } catch (cleanupError) {
                    // The availability warning below also covers cleanup failure.
                }
            }
            storage = candidate || null;
            markUnavailable('availability check', error);
        }
    }

    /**
     * Build the persistent key for a feature.
     *
     * @param {string} feature Lowercase feature identifier (maximum 64 chars).
     * @returns {string} A key in the form "al:<user-key>:<feature>".
     * @throws {TypeError|Error} If the feature or page user key is invalid.
     */
    function key(feature) {
        assertFeature(feature);
        if (!userKey) {
            throw new Error('No valid per-user storage key is available on this page.');
        }
        return persistentKey(feature);
    }

    /**
     * Load JSON-compatible feature data.
     *
     * @param {string} feature Lowercase feature identifier.
     * @param {Object} [options]
     * @param {number} [options.schemaVersion=1] Supported positive schema version.
     * @param {*} [options.defaultValue] JSON-compatible fallback value.
     * @param {function(*): boolean} [options.validate] Data validator.
     * @param {function(*, number, number): *} [options.migrate] Synchronous migrator.
     * @returns {*} A clone of stored, migrated, or default data.
     */
    function load(feature, options) {
        assertFeature(feature);
        const settings = normalizeOptions(options, true);
        const fallback = () => cloneJson(
            settings.defaultValue,
            'defaultValue',
            true,
        );

        let source;
        let envelope;

        if (memory.has(feature)) {
            source = 'memory';
            try {
                envelope = cloneJson(memory.get(feature), 'Storage envelope');
            } catch (error) {
                discard(feature, source);
                warn(
                    feature,
                    'corrupt-value',
                    'The in-memory value is malformed and has been discarded.',
                );
                return fallback();
            }
        } else if (userKey) {
            const result = safeGet(persistentKey(feature));
            if (!result.succeeded || result.value === null) {
                return fallback();
            }

            source = 'persistent';
            try {
                envelope = JSON.parse(result.value);
            } catch (error) {
                discard(feature, source);
                warn(
                    feature,
                    'corrupt-value',
                    'Stored data is not valid JSON and has been removed.',
                );
                return fallback();
            }
        } else {
            return fallback();
        }

        if (!validEnvelope(envelope)) {
            discard(feature, source);
            warn(
                feature,
                'corrupt-value',
                'Stored data has an invalid envelope and has been removed.',
            );
            return fallback();
        }

        if (envelope.schemaVersion > settings.schemaVersion) {
            warn(
                feature,
                'newer-schema',
                `Stored schema version ${envelope.schemaVersion} is newer than supported `
                    + `version ${settings.schemaVersion}; the stored value was left unchanged.`,
            );
            return fallback();
        }

        if (envelope.schemaVersion < settings.schemaVersion) {
            if (!settings.migrate) {
                discard(feature, source);
                warn(
                    feature,
                    'schema-mismatch',
                    `Stored schema version ${envelope.schemaVersion} cannot be migrated to `
                        + `${settings.schemaVersion} and has been removed.`,
                );
                return fallback();
            }

            let migrated;
            try {
                migrated = settings.migrate(
                    cloneJson(envelope.data, 'Stored feature data'),
                    envelope.schemaVersion,
                    settings.schemaVersion,
                );
                migrated = cloneJson(migrated, 'Migrated feature data');
            } catch (error) {
                discard(feature, source);
                warn(
                    feature,
                    'migration-failed',
                    `Stored data migration failed${errorContext(error)}; the value was removed.`,
                );
                return fallback();
            }

            const migrationValidation = validateData(migrated, settings.validate);
            if (!migrationValidation.valid) {
                discard(feature, source);
                warn(
                    feature,
                    'validation-failed',
                    `Migrated data failed validation${errorContext(migrationValidation.error)}; `
                        + 'the value was removed.',
                );
                return fallback();
            }

            persistPrepared(feature, migrated, settings.schemaVersion);
            return cloneJson(migrated, 'Migrated feature data');
        }

        const validation = validateData(envelope.data, settings.validate);
        if (!validation.valid) {
            discard(feature, source);
            warn(
                feature,
                'validation-failed',
                `Stored data failed validation${errorContext(validation.error)}; `
                    + 'the value was removed.',
            );
            return fallback();
        }

        return cloneJson(envelope.data, 'Stored feature data');
    }

    /**
     * Save JSON-compatible feature data.
     *
     * @param {string} feature Lowercase feature identifier.
     * @param {*} data JSON-compatible feature data.
     * @param {Object} [options]
     * @param {number} [options.schemaVersion=1] Positive schema version.
     * @param {function(*): boolean} [options.validate] Data validator.
     * @returns {boolean} True when persisted; false when held in memory only.
     * @throws {TypeError} If the data, options, or validation result is invalid.
     */
    function save(feature, data, options) {
        assertFeature(feature);
        const settings = normalizeOptions(options, false);
        const prepared = cloneJson(data, 'Feature data');
        const validation = validateData(prepared, settings.validate);

        if (!validation.valid) {
            throw new TypeError(
                `Feature data failed validation${errorContext(validation.error)}.`,
            );
        }

        return persistPrepared(feature, prepared, settings.schemaVersion);
    }

    /**
     * Remove a feature value from persistent and in-memory storage.
     *
     * @param {string} feature Lowercase feature identifier.
     * @returns {boolean} True when persistent removal succeeded.
     */
    function remove(feature) {
        assertFeature(feature);
        memory.delete(feature);
        return Boolean(userKey && safeRemove(persistentKey(feature)));
    }

    /**
     * Report whether localStorage is currently available to this page.
     *
     * @returns {boolean}
     */
    function isPersistent() {
        return persistent;
    }

    const api = {key, load, save, remove, isPersistent};
    Object.defineProperty(api, 'userKey', {
        value: userKey,
        writable: false,
        configurable: false,
        enumerable: true,
    });
    Object.freeze(api);
    window.AlphaLabStorage = api;

    if (!meta) {
        warn(
            null,
            'missing-user-key',
            'The per-user storage key meta element is missing; using in-memory storage only.',
            true,
        );
    } else if (!userKey) {
        warn(
            null,
            'invalid-user-key',
            'The per-user storage key is invalid; using in-memory storage only.',
            true,
        );
    } else {
        probeStorage();
    }
}(window, document));
