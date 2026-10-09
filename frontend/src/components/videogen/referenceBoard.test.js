import { describe, expect, it } from "vitest";
import {
  boardFileCount,
  boardProblems,
  boardTags,
  clipFramesAllowed,
  entryFromDocument,
  h3GridFloor,
  h3LatentFrames,
  roomInRow,
  serializeBoard,
} from "./referenceBoard";

const LIMITS = { images: 9, videos: 3, audios: 3, files: 12, video_seconds: [2, 15] };

const board = () => {
  const pic = entryFromDocument({ id: 1, filename: "maya.png" }, "images");
  const clip = { ...entryFromDocument({ id: 2, filename: "walk.mp4" }, "videos"), durationS: 4, hasAudio: true };
  const silent = { ...entryFromDocument({ id: 3, filename: "pan.mp4" }, "videos"), durationS: 3, hasAudio: false };
  const voice = entryFromDocument({ id: 4, filename: "voice.wav" }, "audios");
  return { images: [pic], videos: [clip, silent], audios: [voice] };
};

describe("reference board", () => {
  it("numbers audio the way the graph wires it: clip soundtracks first", () => {
    const b = board();
    const tags = boardTags(b);
    expect(tags[b.images[0].key]).toBe("<Picture 1>");
    expect(tags[b.videos[1].key]).toBe("<Video 2>");
    expect(tags[`${b.videos[0].key}:audio`]).toBe("<Audio 1>");
    expect(tags[`${b.videos[1].key}:audio`]).toBeUndefined(); // a clip with no sound has no tag
    expect(tags[b.audios[0].key]).toBe("<Audio 2>");
  });

  it("counts a separate soundtrack as a file and limits the rows", () => {
    const b = board();
    b.videos[1] = { ...b.videos[1], audio: { ref: "9", fileName: "t.wav" } };
    expect(boardFileCount(b)).toBe(5);
    expect(roomInRow(b, "videos", LIMITS)).toBe(1);
    expect(roomInRow(b, "images", { ...LIMITS, files: 5 })).toBe(0);
  });

  it("names what stops a board from rendering", () => {
    expect(boardProblems({ images: [], videos: [], audios: [{}] }, LIMITS)[0]).toMatch(/at least one picture or clip/);
    const b = board();
    b.videos[0] = { ...b.videos[0], durationS: 1.5 };
    expect(boardProblems(b, LIMITS)).toEqual(["Clip 1 is 1.5 s; clips need at least 2 s."]);
  });

  it("sends refs, roles and names, and a separate track by ref", () => {
    const b = board();
    b.images[0].name = "Maya";
    b.videos[0] = { ...b.videos[0], role: "motion", audio: "none" };
    b.audios[0].speaker = "Maya";
    expect(serializeBoard(b)).toEqual({
      images: [{ ref: "1", role: "keep", name: "Maya", note: "" }],
      videos: [
        { ref: "2", role: "motion", name: "", audio: "none" },
        { ref: "3", role: "subject", name: "", audio: "own" },
      ],
      audios: [{ ref: "4", role: "voice", speaker: "Maya" }],
    });
  });

  it("shares a measured card budget the way the backend does", () => {
    expect([5, 73, 107, 124, 175].map(h3LatentFrames)).toEqual([2, 22, 32, 37, 52]);
    expect(h3GridFloor(48)).toBe(39);
    const perFrame = 54 * 30; // 864x480
    expect(clipFramesAllowed(undefined, 124, 864, 480, 1, 1)).toBeNull();
    expect(clipFramesAllowed(50 * perFrame, 124, 864, 480, 1, 1)).toBe(39);
    expect(clipFramesAllowed(50 * perFrame, 124, 640, 352, 1, 1)).toBeGreaterThan(39);
    const fit = { budget: 50 * perFrame, renderFrames: 124, width: 864, height: 480 };
    const b = board();
    expect(boardProblems(b, LIMITS, fit)[0]).toMatch(/leaves 0.2 s of reference video per clip/);
    expect(boardProblems({ ...b, videos: [b.videos[0]] }, LIMITS, fit)).toEqual([]);
  });
});
