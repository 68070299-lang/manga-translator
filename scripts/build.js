const path = require('path');
const { EXTENSION_DIR, validateExtension } = require('./validate-extension');

const result = validateExtension(EXTENSION_DIR);

if (!result.ok) {
  console.error('Extension build validation failed:');
  for (const error of result.errors) {
    console.error(`- ${error}`);
  }
  process.exit(1);
}

console.log(`Extension manifest validation passed for ${result.files.length} referenced files.`);
console.log('Extension ready at:', path.resolve(EXTENSION_DIR));
