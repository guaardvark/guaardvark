import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import MessageItem from "../MessageItem";
import {
  enableHint,
  enableLabel,
  searchLabel,
  webSearchOfferOf,
  webSearchSend,
} from "../webSearchOffer";

vi.mock("../../../stores/useAppStore", () => ({
  useAppStore: (sel) => {
    const state = { systemLogo: null, activeLessonId: null };
    return typeof sel === "function" ? sel(state) : state;
  },
}));

vi.mock("../../common/NarrateButton", () => ({
  default: () => null,
}));

const QUESTION = "who won the game last night?";

const renderMsg = (message, onSearchWeb) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <MessageItem message={message} sessionId="session_1" onSearchWeb={onSearchWeb} />
    </ThemeProvider>,
  );

const assistant = (overrides) => ({
  role: "assistant",
  content: "I can't see last night's results from here.",
  timestamp: "2026-10-06T12:00:00Z",
  ...overrides,
});

const searchOffer = { action: "search", query: QUESTION };
const enableOffer = { action: "enable_web_access", query: QUESTION };

describe("webSearchOfferOf", () => {
  it("reads the live flag and the saved one", () => {
    expect(webSearchOfferOf(assistant({ web_search_offer: searchOffer }))).toEqual(searchOffer);
    expect(webSearchOfferOf(assistant({ extra_data: { web_search_offer: enableOffer } }))).toEqual(enableOffer);
  });

  it("ignores user rows, missing queries and unknown actions", () => {
    expect(webSearchOfferOf({ role: "user", content: QUESTION, web_search_offer: searchOffer })).toBeNull();
    expect(webSearchOfferOf(assistant({ web_search_offer: { action: "search", query: "  " } }))).toBeNull();
    expect(webSearchOfferOf(assistant({ web_search_offer: { action: "fetch", query: QUESTION } }))).toBeNull();
    expect(webSearchOfferOf(assistant({ web_search_offer: null }))).toBeNull();
    expect(webSearchOfferOf(assistant({}))).toBeNull();
  });
});

describe("webSearchSend", () => {
  it("is the send /websearch makes: one web_search of the question", () => {
    expect(webSearchSend(QUESTION)).toEqual({
      text: `/websearch ${QUESTION}`,
      options: {
        direct_tool: "web_search",
        direct_tool_params: { query: QUESTION },
        slash_command: "websearch",
        slash_args: QUESTION,
      },
    });
  });
});

describe("MessageItem web search offer", () => {
  it("sends nothing until clicked, then one search of the question", () => {
    const onSearchWeb = vi.fn(() => true);
    renderMsg(assistant({ web_search_offer: searchOffer }), onSearchWeb);

    const button = screen.getByTestId("web-search-offer");
    expect(button).toHaveTextContent(searchLabel());
    expect(onSearchWeb).not.toHaveBeenCalled();

    fireEvent.click(button);
    expect(onSearchWeb).toHaveBeenCalledTimes(1);
    expect(onSearchWeb).toHaveBeenCalledWith(QUESTION);

    fireEvent.click(button);
    expect(onSearchWeb).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
  });

  it("stays available when the chat could not send", () => {
    const onSearchWeb = vi.fn(() => false);
    renderMsg(assistant({ web_search_offer: searchOffer }), onSearchWeb);

    const button = screen.getByTestId("web-search-offer");
    fireEvent.click(button);
    expect(button).not.toBeDisabled();
    fireEvent.click(button);
    expect(onSearchWeb).toHaveBeenCalledTimes(2);
  });

  it("shows the offer saved with a reply from history", () => {
    renderMsg(assistant({ extra_data: { web_search_offer: searchOffer } }), vi.fn());
    expect(screen.getByTestId("web-search-offer")).toHaveTextContent(searchLabel());
  });

  it("shows no search offer where the chat cannot send", () => {
    renderMsg(assistant({ web_search_offer: searchOffer }));
    expect(screen.queryByTestId("web-search-offer")).not.toBeInTheDocument();
  });

  it("with web access off says how to turn it on and sends nothing", () => {
    const onSearchWeb = vi.fn(() => true);
    renderMsg(assistant({ web_search_offer: enableOffer }), onSearchWeb);

    const button = screen.getByTestId("web-search-offer");
    expect(button).toHaveTextContent(enableLabel());
    expect(screen.queryByTestId("web-search-offer-hint")).not.toBeInTheDocument();

    fireEvent.click(button);
    expect(screen.getByTestId("web-search-offer-hint")).toHaveTextContent(enableHint());
    expect(onSearchWeb).not.toHaveBeenCalled();
  });

  it("shows nothing for a reply without an offer or for the person's own message", () => {
    renderMsg(assistant({}), vi.fn());
    expect(screen.queryByTestId("web-search-offer")).not.toBeInTheDocument();
    renderMsg({ role: "user", content: QUESTION, web_search_offer: searchOffer }, vi.fn());
    expect(screen.queryByTestId("web-search-offer")).not.toBeInTheDocument();
  });
});
