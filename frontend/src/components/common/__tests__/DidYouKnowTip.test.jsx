import React from "react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import DidYouKnowTip from "../DidYouKnowTip";
import { SHOW_SHORTCUTS_EVENT } from "../KeyboardShortcutsOverlay";
import { readSeenTips, resetTipSessionForTests, SEEN_TIPS_KEY } from "../didYouKnowModel";
import { readStarAsk, recordWin, STAR_ASK_KEY } from "../starAskModel";
import { useAppStore } from "../../../stores/useAppStore";
import { DEFAULT_PROFILE } from "../../../config/profile";

const TIPS = [
  { id: "one", text: "First tip text." },
  { id: "two", text: "Second tip text.", route: "/settings" },
  { id: "three", text: "Third tip text.", action: "shortcuts" },
];

const DELAY = 100;

function Where() {
  const { pathname } = useLocation();
  return <div data-testid="where">{pathname}</div>;
}

const REPO = "https://github.com/guaardvark/guaardvark";

function renderTip(path = "/dashboard", props = {}) {
  return render(
    <ThemeProvider theme={createTheme()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="*" element={<Where />} />
        </Routes>
        <DidYouKnowTip tips={TIPS} delayMs={DELAY} starRepoUrl={REPO} starDelayMs={DELAY} {...props} />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const wait = (ms = DELAY) => act(() => vi.advanceTimersByTime(ms));

beforeEach(() => {
  vi.useFakeTimers();
  window.localStorage.clear();
  window.sessionStorage.clear();
  resetTipSessionForTests();
  useAppStore.setState({
    tipsEnabled: true,
    systemInfoLoaded: true,
    profileFirstRun: false,
    updateNoticeVisible: false,
    profile: DEFAULT_PROFILE,
    navChrome: "sidebar",
    sidebarExpanded: false,
  });
});

afterEach(() => {
  vi.useRealTimers();
  document.querySelectorAll(".MuiDialog-root").forEach((el) => el.remove());
});

describe("DidYouKnowTip", () => {
  it("shows the first unseen tip after the delay and records it as seen", () => {
    window.localStorage.setItem(SEEN_TIPS_KEY, JSON.stringify(["one"]));
    renderTip();
    expect(screen.queryByRole("region", { name: "Did you know" })).toBeNull();
    wait();
    expect(screen.getByText("Second tip text.")).toBeInTheDocument();
    expect(readSeenTips()).toEqual(["one", "two"]);
  });

  it("waits for the first-run profile choice", () => {
    useAppStore.setState({ profileFirstRun: true });
    renderTip();
    wait(DELAY * 10);
    expect(screen.queryByText("First tip text.")).toBeNull();

    act(() => useAppStore.setState({ profileFirstRun: false }));
    wait();
    expect(screen.getByText("First tip text.")).toBeInTheDocument();
  });

  it("waits until the profile is known at all", () => {
    useAppStore.setState({ systemInfoLoaded: false });
    renderTip();
    wait(DELAY * 10);
    expect(screen.queryByText("First tip text.")).toBeNull();
    act(() => useAppStore.setState({ systemInfoLoaded: true }));
    wait();
    expect(screen.getByText("First tip text.")).toBeInTheDocument();
  });

  it("shows nothing when tips are switched off", () => {
    useAppStore.setState({ tipsEnabled: false });
    renderTip();
    wait(DELAY * 10);
    expect(screen.queryByRole("region", { name: "Did you know" })).toBeNull();
    expect(readSeenTips()).toEqual([]);
  });

  it("Next tip moves on without repeating", () => {
    renderTip();
    wait();
    fireEvent.click(screen.getByRole("button", { name: "Next tip" }));
    expect(screen.getByText("Second tip text.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next tip" }));
    expect(screen.getByText("Third tip text.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next tip" }));
    expect(screen.getByText("First tip text.")).toBeInTheDocument();
    expect(readSeenTips()).toEqual(["two", "three", "one"]);
  });

  it("Don't show tips turns them off for good", () => {
    renderTip();
    wait();
    fireEvent.click(screen.getByRole("button", { name: "Don't show tips" }));
    expect(screen.queryByRole("region", { name: "Did you know" })).toBeNull();
    expect(useAppStore.getState().tipsEnabled).toBe(false);
    expect(JSON.parse(window.localStorage.getItem("guaardvark-app-storage")).state.tipsEnabled).toBe(false);
  });

  it("shows one tip per session: closing it does not bring another", () => {
    const { unmount } = renderTip();
    wait();
    fireEvent.click(screen.getByRole("button", { name: "Close tip" }));
    expect(screen.queryByRole("region", { name: "Did you know" })).toBeNull();
    wait(DELAY * 10);
    expect(screen.queryByRole("region", { name: "Did you know" })).toBeNull();

    unmount();
    renderTip();
    wait(DELAY * 10);
    expect(screen.queryByRole("region", { name: "Did you know" })).toBeNull();
  });

  it("Show me opens the tip's page", () => {
    window.localStorage.setItem(SEEN_TIPS_KEY, JSON.stringify(["one"]));
    renderTip();
    wait();
    fireEvent.click(screen.getByRole("button", { name: "Show me" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/settings");
    expect(screen.queryByRole("region", { name: "Did you know" })).toBeNull();
  });

  it("Show me can run an action instead of opening a page", () => {
    window.localStorage.setItem(SEEN_TIPS_KEY, JSON.stringify(["one", "two"]));
    const onShow = vi.fn();
    window.addEventListener(SHOW_SHORTCUTS_EVENT, onShow);
    renderTip();
    wait();
    fireEvent.click(screen.getByRole("button", { name: "Show me" }));
    expect(onShow).toHaveBeenCalledTimes(1);
    window.removeEventListener(SHOW_SHORTCUTS_EVENT, onShow);
  });

  it("has no Show me for a tip without a page or action", () => {
    renderTip();
    wait();
    expect(screen.getByText("First tip text.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show me" })).toBeNull();
  });

  it("does not open over a dialog", () => {
    const dialog = document.createElement("div");
    dialog.className = "MuiDialog-root MuiModal-root";
    document.body.appendChild(dialog);
    renderTip();
    wait();
    expect(screen.queryByText("First tip text.")).toBeNull();

    dialog.remove();
    wait(5000);
    expect(screen.getByText("First tip text.")).toBeInTheDocument();
  });

  it("steps aside while the update notice is up", () => {
    renderTip();
    wait();
    expect(screen.getByText("First tip text.")).toBeInTheDocument();
    act(() => useAppStore.setState({ updateNoticeVisible: true }));
    expect(screen.queryByText("First tip text.")).toBeNull();
    act(() => useAppStore.setState({ updateNoticeVisible: false }));
    expect(screen.getByText("First tip text.")).toBeInTheDocument();
  });

  it("stays off the chat page", () => {
    renderTip("/chat");
    wait(DELAY * 10);
    expect(screen.queryByRole("region", { name: "Did you know" })).toBeNull();
  });

  it("sits to the right of the sidebar", () => {
    useAppStore.setState({ sidebarExpanded: true });
    renderTip();
    wait();
    expect(screen.getByRole("region", { name: "Did you know" })).toHaveStyle({ left: "256px" });
  });
});

describe("DidYouKnowTip star ask", () => {
  const STAR = { name: "Star on GitHub" };
  const setStarState = (state) => window.localStorage.setItem(STAR_ASK_KEY, JSON.stringify(state));

  it("asks in place of the session's tip once a render has finished", () => {
    setStarState({ wins: 1 });
    renderTip();
    wait();
    expect(screen.getByRole("region", STAR)).toBeInTheDocument();
    expect(screen.getByText("If Guaardvark is useful to you, a star on GitHub helps other people find it.")).toBeInTheDocument();
    expect(screen.queryByText("First tip text.")).toBeNull();
    expect(readStarAsk().asks).toBe(1);
  });

  it("asks on the third day of use", () => {
    setStarState({ dayCount: 2, lastDay: "2000-01-01" });
    renderTip();
    wait();
    expect(screen.getByRole("region", STAR)).toBeInTheDocument();
    expect(readStarAsk().dayCount).toBe(3);
  });

  it("does not ask a new browser on its first day", () => {
    renderTip();
    wait();
    expect(screen.getByText("First tip text.")).toBeInTheDocument();
    expect(screen.queryByRole("region", STAR)).toBeNull();
  });

  it("asks right after a render finishes when no card is showing", () => {
    renderTip();
    wait();
    fireEvent.click(screen.getByRole("button", { name: "Close tip" }));
    act(() => {
      recordWin("image_generation");
    });
    expect(screen.queryByRole("region", STAR)).toBeNull();
    wait();
    expect(screen.getByRole("region", STAR)).toBeInTheDocument();
  });

  it("does not cover a tip on screen; the next session asks instead", () => {
    const { unmount } = renderTip();
    wait();
    act(() => {
      recordWin("video_gen");
    });
    wait();
    expect(screen.getByText("First tip text.")).toBeInTheDocument();
    expect(screen.queryByRole("region", STAR)).toBeNull();

    unmount();
    window.sessionStorage.clear();
    resetTipSessionForTests();
    renderTip();
    wait();
    expect(screen.getByRole("region", STAR)).toBeInTheDocument();
  });

  it("Star on GitHub opens the project page and never asks again", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    setStarState({ wins: 1 });
    renderTip();
    wait();
    fireEvent.click(screen.getByRole("button", STAR));
    expect(open).toHaveBeenCalledWith(REPO, "_blank", "noopener,noreferrer");
    expect(screen.queryByRole("region", STAR)).toBeNull();
    expect(readStarAsk().done).toBe(true);
    open.mockRestore();
  });

  it("Later leaves one more ask, five days of use away", () => {
    setStarState({ wins: 1, dayCount: 4, lastDay: "2000-01-01" });
    const { unmount } = renderTip();
    wait();
    fireEvent.click(screen.getByRole("button", { name: "Later" }));
    expect(screen.queryByRole("region", STAR)).toBeNull();
    expect(readStarAsk()).toMatchObject({ asks: 1, done: false });

    unmount();
    window.sessionStorage.clear();
    resetTipSessionForTests();
    setStarState({ ...readStarAsk(), dayCount: readStarAsk().dayCount + 4, lastDay: "2000-01-02" });
    renderTip();
    wait();
    expect(screen.getByRole("region", STAR)).toBeInTheDocument();
    expect(readStarAsk().asks).toBe(2);
  });

  it("Don't ask again ends it", () => {
    setStarState({ wins: 1 });
    renderTip();
    wait();
    fireEvent.click(screen.getByRole("button", { name: "Don't ask again" }));
    expect(screen.queryByRole("region", STAR)).toBeNull();
    expect(readStarAsk().done).toBe(true);
  });

  it("a rebranded build never asks", () => {
    setStarState({ wins: 3, dayCount: 9 });
    renderTip("/dashboard", { starRepoUrl: null });
    wait();
    expect(screen.getByText("First tip text.")).toBeInTheDocument();
    expect(screen.queryByRole("region", STAR)).toBeNull();
  });

  it("follows the tips switch", () => {
    useAppStore.setState({ tipsEnabled: false });
    setStarState({ wins: 1 });
    renderTip();
    wait(DELAY * 10);
    expect(screen.queryByRole("region", STAR)).toBeNull();
    expect(readStarAsk().asks).toBe(0);
  });
});
