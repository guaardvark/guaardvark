import { createContext, useContext } from "react";

/**
 * What a plan card in the message list can ask the chat page to do: show a
 * started run, put text in the input for "keep planning", stop the run, and
 * take in a changed or new plan. ChatPage provides it; a list without it
 * (the floating chat) still shows the card, whose own buttons call the API.
 */
export const PlanActionsContext = createContext(null);

export const usePlanActions = () => useContext(PlanActionsContext);
