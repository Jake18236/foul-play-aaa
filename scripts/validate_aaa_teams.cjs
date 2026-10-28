// Local, offline, whole-team validation. No ladder or remote battle service.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(process.argv[2]);
const {TeamValidator} = require(path.join(root, 'dist/sim'));
const validator = new TeamValidator('gen9almostanyability');
const teams = JSON.parse(fs.readFileSync(0, 'utf8'));
const hash = crypto.createHash('sha256');
function digest(dir) {
  for (const name of fs.readdirSync(dir).sort()) {
    const file = path.join(dir, name);
    if (fs.statSync(file).isDirectory()) digest(file);
    else { hash.update(path.relative(root, file)); hash.update(fs.readFileSync(file)); }
  }
}
digest(path.join(root, 'dist/sim'));
digest(path.join(root, 'dist/data'));
digest(path.join(root, 'dist/config'));
const results = teams.map(team => {
  const copy = JSON.parse(JSON.stringify(team));
  return {team, problems: validator.validateTeam(copy) || []};
});
console.log(JSON.stringify({format: 'gen9almostanyability', validator_sha256: hash.digest('hex'), results}));
