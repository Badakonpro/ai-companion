const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL).replace(/\/$/, "");
export const CHAT_ENDPOINT = `${API_BASE_URL}/api/chat`;
export const HEALTH_ENDPOINT = `${API_BASE_URL}/api/health`;
export const STORY_TURN_ENDPOINT = `${API_BASE_URL}/api/story/turn`;
export const STORY_STREAM_ENDPOINT = `${API_BASE_URL}/api/story/turn/stream`;
export const STORY_CHARACTERS_ENDPOINT = `${API_BASE_URL}/api/story/characters`;
export const STORY_SEEDS_ENDPOINT = `${API_BASE_URL}/api/story/seeds`;
export const STORY_SEEDS_GENERATE_ENDPOINT = `${API_BASE_URL}/api/story/seeds/generate`;
export const STORY_TAGS_ENDPOINT = `${API_BASE_URL}/api/story/tags`;
export const SESSIONS_ENDPOINT = `${API_BASE_URL}/api/sessions`;
export const MODEL_CONFIG_ENDPOINT = `${API_BASE_URL}/api/config/model`;

export const snapshotsEndpoint = (sessionId: string) =>
  `${SESSIONS_ENDPOINT}/${encodeURIComponent(sessionId)}/snapshots`;

export const restoreSnapshotEndpoint = (sessionId: string, snapshotId: number) =>
  `${SESSIONS_ENDPOINT}/${encodeURIComponent(sessionId)}/snapshots/${snapshotId}/restore`;
