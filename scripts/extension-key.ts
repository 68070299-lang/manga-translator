/**
 * Fixed manifest `key` for the unpacked build (dist/chrome-mv3).
 *
 * Chrome derives an unpacked extension's ID from its folder path unless the
 * manifest has a key, and each ID has its own storage (settings, API keys, the
 * backend token, enabled sites, cache). The key keeps one ID wherever the folder
 * lives. Only the public half exists: unpacked loading needs nothing else.
 *
 * wxt.config.ts adds it to the promoted unpacked copy only. The validated
 * staging build that `wxt zip` packs never has it, because the Chrome Web Store
 * rejects uploads whose manifest contains a key.
 */
export const UNPACKED_EXTENSION_KEY =
  'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwVZpcoj94SdSz1lrh3g+hpCVy7ovL7CKW2iYRtxsbzDMOs4FfSrmsmVKyV5CAr2n9nH0BZDQhs7YIZ2gPuk8SZPJFiSBzb45V7pYSpOhNh7Hlp1yNvmMGU2ZrT1pGD5TilPZuibCd/9C/CDM14WH2jmjaWfOLcHtyvYCPdEk8aq2nocfqCcJPY6TrN5FJpzZKuWwrRfTzMupF6CyC23ZOPjjd2qzY993yzEm4hqpqepJTdZ4vAVxmDiFdzGgt5hbk/tt7DpVmYX0P4HyZEO+WEIUZW/8DsweF69SQBG5gyWEzlt5UzDD95W0m36rutGGR31BtrqDocMEE/KkDwVYCwIDAQAB';

/** The ID Chrome derives from UNPACKED_EXTENSION_KEY (asserted in tests/unit/manifest.test.ts). */
export const UNPACKED_EXTENSION_ID = 'jnakpckdlnjolbfdggkifidibbfenhdh';
