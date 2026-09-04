// Live counts for the badge keys that catalog entries name (`badge: "..."`).
// A distribution replaces this file the way it replaces brand.jsx. It is a
// separate file because a brand's hook will import the app store and API
// services, and those import brand.jsx — putting the hook on the brand object
// would make that a cycle. The hook is called once per render of the
// Workspaces bar and returns { badgeKey: count }.
const EMPTY = Object.freeze({});

export const useNavBadgeCounts = () => EMPTY;
