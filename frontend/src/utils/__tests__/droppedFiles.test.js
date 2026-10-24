import { describe, it, expect } from "vitest";
import {
  snapshotDrop,
  collectDroppedFiles,
  readEntryFiles,
  dragCarriesFiles,
  ensureFolderPath,
  storedFolderName,
} from "../droppedFiles";

const fileEntry = (name) => {
  const file = new File([name], name);
  return { isFile: true, isDirectory: false, name, file: (ok) => setTimeout(() => ok(file), 0) };
};

const dirEntry = (name, children, batchSize = 2) => ({
  isFile: false,
  isDirectory: true,
  name,
  createReader: () => {
    let offset = 0;
    return {
      readEntries: (ok) =>
        setTimeout(() => {
          const batch = children.slice(offset, offset + batchSize);
          offset += batch.length;
          ok(batch);
        }, 0),
    };
  },
});

// A DataTransfer the way the browser hands it to a drop handler: one item list
// object, readable during the event and empty once the handler awaits.
const dropTransfer = (entries) => {
  let live = true;
  const items = {
    get length() {
      return live ? entries.length : 0;
    },
  };
  entries.forEach((entry, index) => {
    Object.defineProperty(items, index, {
      get: () => (live
        ? { kind: "file", webkitGetAsEntry: () => (live ? entry : null), getAsFile: () => null }
        : undefined),
    });
  });
  const transfer = {
    types: ["Files"],
    items,
    get files() {
      return live ? entries.filter((e) => e.isFile).map((e) => new File([e.name], e.name)) : [];
    },
  };
  return { transfer, endEvent: () => { live = false; } };
};

describe("snapshotDrop", () => {
  it("keeps every dropped item after the event's item list is emptied", async () => {
    const { transfer, endEvent } = dropTransfer([fileEntry("a.txt"), fileEntry("b.txt"), fileEntry("c.txt")]);

    const snapshot = snapshotDrop(transfer);
    await Promise.resolve();
    endEvent();
    const files = await collectDroppedFiles(snapshot);

    expect(files.map((f) => f.relativePath)).toEqual(["a.txt", "b.txt", "c.txt"]);
  });

  it("falls back to dataTransfer.files when the items expose nothing", async () => {
    const a = new File(["x"], "a.txt");
    const b = new File(["y"], "b.txt");
    const snapshot = snapshotDrop({ types: ["Files"], items: [], files: [a, b] });

    const files = await collectDroppedFiles(snapshot);

    expect(files.map((f) => f.file)).toEqual([a, b]);
  });

  it("takes file items without an entry as plain files", async () => {
    const loose = new File(["z"], "loose.txt");
    const snapshot = snapshotDrop({
      types: ["Files"],
      items: [{ kind: "file", getAsFile: () => loose }, { kind: "string" }],
      files: [loose],
    });

    expect(await collectDroppedFiles(snapshot)).toEqual([{ file: loose, relativePath: "loose.txt" }]);
  });
});

describe("readEntryFiles", () => {
  it("walks folders in every batch, each file once, with paths under the dropped folder", async () => {
    const tree = dirEntry("Photos", [
      fileEntry("1.jpg"),
      fileEntry("2.jpg"),
      dirEntry("Trip", [fileEntry("3.jpg")]),
      fileEntry("4.jpg"),
    ]);

    const files = await readEntryFiles(tree);

    expect(files.map((f) => f.relativePath)).toEqual([
      "Photos/1.jpg",
      "Photos/2.jpg",
      "Photos/Trip/3.jpg",
      "Photos/4.jpg",
    ]);
  });
});

describe("storedFolderName", () => {
  it("matches the names the folder route stores (werkzeug secure_filename)", () => {
    expect(storedFolderName("Weekend in Lisbon")).toBe("Weekend_in_Lisbon");
    expect(storedFolderName("  Café  déjà vu ")).toBe("Cafe_deja_vu");
    expect(storedFolderName("Été 2024 (1)")).toBe("Ete_2024_1");
    expect(storedFolderName("..hidden.")).toBe("hidden");
    expect(storedFolderName("__x__")).toBe("x");
    expect(storedFolderName("Plain")).toBe("Plain");
  });
});

// The Files API as the backend behaves: a new folder is stored under its
// secure_filename name, and creating one that exists is a 409.
const fakeFilesApi = (existing = []) => {
  const folders = [...existing];
  const calls = { list: 0, create: [] };
  return {
    folders,
    calls,
    listFolders: async (parent) => {
      calls.list += 1;
      return folders.filter((f) => f.parent === parent);
    },
    createFolder: async (name, parent) => {
      calls.create.push(name);
      const stored = name.trim().split(/\s+/).join("_");
      const path = parent === "/" ? stored : `${parent}/${stored}`;
      if (folders.some((f) => f.path === path)) {
        const err = new Error("Folder already exists");
        err.response = { status: 409 };
        throw err;
      }
      const folder = { name: stored, path, parent };
      folders.push(folder);
      return folder;
    },
  };
};

describe("ensureFolderPath", () => {
  it("puts every file of a dropped folder with spaces in its name into one folder", async () => {
    const api = fakeFilesApi();
    const cache = new Map();
    const paths = [];
    for (let i = 0; i < 3; i += 1) {
      paths.push(await ensureFolderPath("Weekend in Lisbon", "/", api, cache));
    }
    expect(paths).toEqual(["Weekend_in_Lisbon", "Weekend_in_Lisbon", "Weekend_in_Lisbon"]);
    expect(api.calls.create).toEqual(["Weekend in Lisbon"]);
  });

  it("finds the stored folder again on a later drop", async () => {
    const api = fakeFilesApi([{ name: "Weekend_in_Lisbon", path: "Weekend_in_Lisbon", parent: "/" }]);
    expect(await ensureFolderPath("Weekend in Lisbon", "/", api)).toBe("Weekend_in_Lisbon");
    expect(api.calls.create).toEqual([]);
  });

  it("creates nested folders under the drop target and reuses them", async () => {
    const api = fakeFilesApi([{ name: "Photos", path: "Photos", parent: "/" }]);
    const cache = new Map();
    expect(await ensureFolderPath("Summer trip/Day one", "Photos", api, cache)).toBe("Photos/Summer_trip/Day_one");
    expect(await ensureFolderPath("Summer trip/Day two", "Photos", api, cache)).toBe("Photos/Summer_trip/Day_two");
    expect(api.calls.create).toEqual(["Summer trip", "Day one", "Day two"]);
  });

  it("uses the existing folder when creating it reports a conflict", async () => {
    // Another upload created the folder between the lookup and the create.
    const api = fakeFilesApi();
    const listFolders = api.listFolders;
    let looks = 0;
    api.listFolders = async (parent) => {
      looks += 1;
      if (looks === 1) {
        api.folders.push({ name: "Odd_Name", path: "Odd_Name", parent: "/" });
        return [];
      }
      return listFolders(parent);
    };
    expect(await ensureFolderPath("Odd Name", "/", api)).toBe("Odd_Name");
    expect(looks).toBe(2);
  });

  it("passes on errors other than a conflict", async () => {
    const api = {
      listFolders: async () => [],
      createFolder: async () => {
        const err = new Error("Invalid folder name");
        err.response = { status: 400 };
        throw err;
      },
    };
    await expect(ensureFolderPath("???", "/", api)).rejects.toThrow("Invalid folder name");
  });
});

describe("dragCarriesFiles", () => {
  it("is true only for drags from the operating system", () => {
    expect(dragCarriesFiles({ dataTransfer: { types: ["Files"] } })).toBe(true);
    expect(dragCarriesFiles({ dataTransfer: { types: ["text/plain"] } })).toBe(false);
    expect(dragCarriesFiles({})).toBe(false);
  });
});

describe("collectDroppedFiles with prune", () => {
  it("leaves out hidden, dependency and build folders, credentials and compiled files", async () => {
    const repo = dirEntry("repo", [
      fileEntry("README.md"),
      dirEntry("service", [fileEntry("app.py"), dirEntry("__pycache__", [fileEntry("app.cpython-312.pyc")])]),
      dirEntry(".git", [fileEntry("HEAD")]),
      dirEntry("node_modules", [fileEntry("index.js")]),
      fileEntry(".env"),
      fileEntry("server.pem"),
      fileEntry("stray.pyc"),
    ]);
    const prune = { folders: 0, files: 0 };
    const files = await collectDroppedFiles({ entries: [repo], looseFiles: [], fallbackFiles: [] }, { prune });
    expect(files.map((f) => f.relativePath).sort()).toEqual(["repo/README.md", "repo/service/app.py"]);
    expect(prune).toEqual({ folders: 3, files: 3 });
  });

  it("keeps everything without prune", async () => {
    const repo = dirEntry("repo", [fileEntry(".env"), dirEntry("node_modules", [fileEntry("index.js")])]);
    const files = await collectDroppedFiles({ entries: [repo], looseFiles: [], fallbackFiles: [] });
    expect(files.map((f) => f.relativePath).sort()).toEqual(["repo/.env", "repo/node_modules/index.js"]);
  });
});
