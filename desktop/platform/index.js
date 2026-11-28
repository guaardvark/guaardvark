// Picks the platform module. Each one implements the same interface:
//   describe()      -> { installRoot, webUrl, apiUrl }
//   isInstalled()   -> Promise<boolean>
//   install(report) -> Promise<void>   first-run install, reporting progress events
//   isRunning()     -> Promise<boolean>
//   start(report)   -> Promise<{ webUrl, apiUrl }>, rejects with the "failed" event
//   stop()          -> Promise<void>   stops only what this app started
//   logPath()       -> string          the log the "Show log" button opens
// Progress events follow desktop/PROTOCOL.md.

const modules = {
  linux: './linux',
  win32: './windows',
  darwin: './darwin',
};

function load(platform = process.platform) {
  const path = modules[platform];
  if (!path) throw new Error(`Guaardvark's desktop app does not support ${platform} yet`);
  return require(path);
}

module.exports = { load };
