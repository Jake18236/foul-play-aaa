// Validate the synthetic set pool with an explicitly supplied Showdown checkout.
// Usage: node scripts/validate_bnb_sets.cjs /path/to/pokemon-showdown < sets.json
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const root = path.resolve(process.argv[2]);
const {TeamValidator} = require(path.join(root, 'dist/sim'));
const catalog = JSON.parse(fs.readFileSync(0, 'utf8'));
const validator = new TeamValidator('gen9badnboosted');
const results = catalog.map(p => {
    const set = {species: p.species, ability: p.ability, item: p.item,
        nature: p.nature, moves: p.moves.split(' '), level: 100,
        evs: {hp: 252, atk: 0, def: 0, spa: 252, spd: 4, spe: 0}};
    return {species: p.species, problems: validator.validateSet(set) || []};
});
const revision = execFileSync('git', ['rev-parse', 'HEAD'], {cwd: root, encoding: 'utf8'}).trim();
console.log(JSON.stringify({showdown_revision: revision, format: 'gen9badnboosted',
    catalog, sets: results}, null, 2));
process.exitCode = results.some(r => r.problems.length) ? 1 : 0;
