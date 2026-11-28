// The app's own screens' line to the app. Pages served by the studio get nothing.

const { contextBridge, ipcRenderer } = require('electron');

if (window.location.protocol === 'app:' && window.location.host === 'guaardvark') {
  contextBridge.exposeInMainWorld('guaardvarkDesktop', {
    getUi: () => ipcRenderer.invoke('gv:get-ui'),
    onUi: (handler) => ipcRenderer.on('gv:ui', (_event, ui) => handler(ui)),
    retry: () => ipcRenderer.send('gv:retry'),
    install: (choice) => ipcRenderer.send('gv:install', choice),
    restart: () => ipcRenderer.send('gv:restart'),
    showLog: () => ipcRenderer.send('gv:show-log'),
    quit: () => ipcRenderer.send('gv:quit'),
  });
}
