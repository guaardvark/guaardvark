// The chat's feature grid: the things chat can do, one tile each, opened from
// the grid icon beside the message box. A tile does one of four things:
//   prefill  puts a slash command in the box, cursor after it
//   starter  puts the start of a plain request in the box (its text is a
//            translated string, chat.features.<id>.starter)
//   attach   opens the file picker
//   mode     switches a chat mode on or off (shown as a chip while on)
// `needs` names chat tools (GET /api/tools) the tile cannot work without; a
// tile whose tool this install lacks is left out. `webAccess` tiles are shown
// but disabled while web access is off, since that is a setting to switch on.
import AutoFixHighIcon from "@mui/icons-material/AutoFixHigh";
import BubbleChartIcon from "@mui/icons-material/BubbleChart";
import ChecklistIcon from "@mui/icons-material/Checklist";
import DesktopWindowsIcon from "@mui/icons-material/DesktopWindows";
import HiveIcon from "@mui/icons-material/Hive";
import ImageIcon from "@mui/icons-material/Image";
import LocalMoviesIcon from "@mui/icons-material/LocalMovies";
import ManageSearchIcon from "@mui/icons-material/ManageSearch";
import MusicVideoIcon from "@mui/icons-material/MusicVideo";
import NoteAddIcon from "@mui/icons-material/NoteAdd";
import TravelExploreIcon from "@mui/icons-material/TravelExplore";
import VideoCameraBackIcon from "@mui/icons-material/VideoCameraBack";

export const CHAT_FEATURES = [
  { id: "image", Icon: ImageIcon, prefill: "/imagine ", needs: ["generate_image"] },
  { id: "editImage", Icon: AutoFixHighIcon, attach: true, needs: ["edit_image"] },
  { id: "video", Icon: VideoCameraBackIcon, prefill: "/video ", needs: ["generate_video"] },
  { id: "musicVideo", Icon: MusicVideoIcon, prefill: "/music-video ", needs: ["generate_music_video"] },
  { id: "filmCrew", Icon: LocalMoviesIcon, prefill: "/film-crew ", needs: ["start_film_crew"] },
  { id: "file", Icon: NoteAddIcon, starter: true, needs: ["generate_file"] },
  { id: "analyze", Icon: ManageSearchIcon, attach: true },
  { id: "webSearch", Icon: TravelExploreIcon, prefill: "/websearch ", needs: ["web_search"], webAccess: true },
  { id: "code", Icon: BubbleChartIcon, starter: true, needs: ["system_map_query"] },
  { id: "swarm", Icon: HiveIcon, starter: true, needs: ["launch_swarm"] },
  { id: "agent", Icon: DesktopWindowsIcon, mode: "agent", needs: ["agent_task_execute"] },
  { id: "plan", Icon: ChecklistIcon, mode: "plan" },
];

// Tiles per row in the grid.
export const CHAT_FEATURE_COLUMNS = 4;
