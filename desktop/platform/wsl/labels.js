// Loading-screen text for the Windows steps. Step ids are stable; the
// labels are what a person reads.

const LABELS = {
  windows_version: 'Checking the Windows version',
  nvidia_driver: 'Checking the NVIDIA graphics driver',
  virtualization: 'Checking that this PC can run virtual machines',
  wsl_check: 'Checking Windows Subsystem for Linux',
  wsl_enable: 'Turning on Windows Subsystem for Linux',
  wsl_update: 'Updating Windows Subsystem for Linux',
  restart_needed: 'Restart needed',
  disk_space: 'Checking free disk space',
  wsl_image_download: "Downloading Guaardvark's Linux system",
  wsl_image_verify: 'Checking the download',
  wsl_import: "Setting up Guaardvark's Linux system",
  ubuntu_install: 'Installing Ubuntu for Guaardvark',
  ubuntu_prepare: 'Preparing Ubuntu for Guaardvark',
  guaardvark_download: 'Downloading Guaardvark',
  wsl_first_boot: "Starting Guaardvark's Linux system",
  distro_gpu: 'Checking that Linux can see the graphics card',
  wsl_installed: "Guaardvark's Linux system is ready",
  wsl_attach: "Connecting to Guaardvark's Linux system",
  wsl_keep_alive: 'Keeping Guaardvark running in the background',
  wsl_memory: 'Setting how much memory Linux may use',
  stop_pending: 'Waiting for Guaardvark to finish stopping',
};

// Error codes WSL prints after "Error code:" (Wsl/.../CODE). They are not
// translated, unlike the message text around them, so they are what the
// setup matches on.
const WSL_ERRORS = [
  {
    match: /HCS_E_HYPERV_NOT_INSTALLED|0x80370102/i,
    kind: 'virtualization',
    label: 'Virtualization is turned off',
    detail: "Windows cannot start Linux because virtualization is off. Restart the PC, open its firmware settings (BIOS/UEFI, usually F2 or Del while it starts), turn on Intel Virtualization Technology (VT-x) or AMD SVM, save, and open Guaardvark again. If it is already on, make sure the Windows feature 'Virtual Machine Platform' is turned on.",
  },
  {
    match: /WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED|WSL_E_NOT_INSTALLED|0x8007019e/i,
    kind: 'not_installed',
    label: 'Windows Subsystem for Linux is not turned on',
    detail: 'Guaardvark will turn it on. Windows asks for permission and then needs a restart.',
  },
  {
    match: /ERROR_DISK_FULL|0x80070070/i,
    kind: 'disk_full',
    label: 'The disk is full',
    detail: 'Free some space on the drive that holds your user folder and try again.',
  },
  {
    match: /WSL_E_DISTRO_NOT_FOUND/i,
    kind: 'distro_missing',
    label: "Guaardvark's Linux system is missing",
    detail: 'Run setup again from the Guaardvark window.',
  },
  {
    match: /ERROR_ALREADY_EXISTS|0x800700b7/i,
    kind: 'distro_exists',
    label: "Guaardvark's Linux system already exists",
    detail: '',
  },
];

function classify(text) {
  for (const entry of WSL_ERRORS) {
    if (entry.match.test(text || '')) return entry;
  }
  return null;
}

function event(step, state, extra = {}) {
  return { t: Date.now() / 1000, step, state, label: LABELS[step] || step, ...extra };
}

module.exports = { LABELS, WSL_ERRORS, classify, event };
