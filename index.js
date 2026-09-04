const fs = require('fs')

// Fork addition: directories that are tooling, not icons, must stay out of the indexes.
const IGNORED = new Set(['node_modules', 'tools', 'docs', '.git', '.github'])

module.exports.update = async function update() {
  const sorter = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })
await (async function checkFolders (folder) {
    folder = folder.replace('//', '/')
    const files = await fs.promises.readdir(folder)
    let newJson = {}
    let hasSubFolders = false

    await Promise.all(files.map(async file => {
      if (!file.includes('.') && !IGNORED.has(file)) {
        hasSubFolders = true
        newJson[file] = await checkFolders(`${folder}/${file}`)
      }
    }))
    if (!hasSubFolders) {
      newJson = files.filter(file => file.includes('.webp')).sort(sorter.compare)
    } else {
      const tempJson = newJson
      const sortedKeys = Object.keys(newJson).sort(sorter.compare)
      newJson = {}
      sortedKeys.forEach(key => (newJson[key] = tempJson[key]))
    }
    fs.writeFile(`./${folder === './' ? '' : `${folder}/`}index.json`, JSON.stringify(newJson, null, 2), 'utf8', () => {})
    return newJson
  })('./')
}
