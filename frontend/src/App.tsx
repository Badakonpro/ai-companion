import React, { useEffect, useRef, useState } from 'react';
import './index.css';
import { SESSIONS_ENDPOINT, STORY_SEEDS_GENERATE_ENDPOINT, STORY_STREAM_ENDPOINT, STORY_TAGS_ENDPOINT, STORY_TURN_ENDPOINT, snapshotsEndpoint, restoreSnapshotEndpoint } from './lib/config';

interface Message {
  id: string;
  role: 'user' | 'narrator' | 'protagonist';
  content: string;
}

interface StoryChoice {
  id: string;
  title: string;
  description?: string;
}

interface StorySeed {
  id: string;
  title: string;
  description?: string;
  world_seed?: string;
  personality?: string;
}

interface ThemeTag {
  id: string;
  label: string;
  icon: string;
}

interface SavedSession {
  session_id: string;
  title: string;
  seed: StorySeed | null;
  nsfw_level: string;
  created_at: string;
  updated_at: string;
  state: Record<string, number>;
  turn_count: number;
}

interface StoryState {
  tension: number;
  trust: number;
  progress: number;
}

interface EmotionState {
  affection: number;
  tension: number;
  trust: number;
  comfort: number;
}

interface ArcInfo {
  arc_number: number;
  arc_title: string;
  arc_status: string;
  suggest_completion: boolean;
}

interface Snapshot {
  id: number;
  session_id: string;
  label: string;
  turn_number: number;
  created_at: string;
}

interface StoryStreamState {
  narrative: string;
  protagonistAction: string;
  choices: StoryChoice[];
}

type AppStage = 'lobby' | 'story';
type NsfwLevel = 'mild' | 'moderate' | 'explicit';

const NSFW_LABELS: Record<NsfwLevel, string> = {
  mild: '🌙 轻度暧昧',
  moderate: '🔥 中度情欲',
  explicit: '💋 高度露骨',
};

const DEFAULT_MESSAGES: Message[] = [
  {
    id: '1',
    role: 'narrator',
    content: '夜色还没完全落下，窗外霓虹已经亮了。你站在故事的入口，等一扇门先打开。',
  },
];

function App() {
  const [stage, setStage] = useState<AppStage>('lobby');
  const [messages, setMessages] = useState<Message[]>(DEFAULT_MESSAGES);
  const [choices, setChoices] = useState<StoryChoice[]>([]);
  const [activeCharacters, setActiveCharacters] = useState<string[]>(['protagonist', 'linxi']);
  const [activeSeed, setActiveSeed] = useState<StorySeed | null>(null);
  const [themeTags, setThemeTags] = useState<ThemeTag[]>([]);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [nsfwLevel, setNsfwLevel] = useState<NsfwLevel>('mild');
  const [userHint, setUserHint] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [candidateSeeds, setCandidateSeeds] = useState<StorySeed[]>([]);
  const [savedSessions, setSavedSessions] = useState<SavedSession[]>([]);
  const [storyState, setStoryState] = useState<StoryState>({ tension: 0, trust: 0, progress: 0 });
  const [emotionState, setEmotionState] = useState<EmotionState>({ affection: 0.3, tension: 0.3, trust: 0.4, comfort: 0.5 });
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [sessionId, setSessionId] = useState(() => `sess_${Date.now()}`);
  const [showDashboard, setShowDashboard] = useState(false);
  const [initialAffection, setInitialAffection] = useState(0.3);
  const [randomPersonality, setRandomPersonality] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [arcInfo, setArcInfo] = useState<ArcInfo | null>(null);
  const [arcCompleting, setArcCompleting] = useState(false);
  const [newArcGenerating, setNewArcGenerating] = useState(false);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [showSnapshots, setShowSnapshots] = useState(false);
  const [snapshotSaving, setSnapshotSaving] = useState(false);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping]);

  useEffect(() => {
    const loadTags = async () => {
      try {
        const response = await fetch(STORY_TAGS_ENDPOINT);
        if (!response.ok) return;
        const data = await response.json();
        if (Array.isArray(data?.tags)) {
          setThemeTags(data.tags);
        }
      } catch (error) {
        console.warn('Failed to load theme tags', error);
      }
    };

    const loadSessions = async () => {
      try {
        const response = await fetch(SESSIONS_ENDPOINT);
        if (!response.ok) return;
        const data = await response.json();
        if (Array.isArray(data?.sessions)) {
          setSavedSessions(data.sessions);
        }
      } catch (error) {
        console.warn('Failed to load sessions', error);
      }
    };

    void loadTags();
    void loadSessions();
  }, []);

  const startFromSeed = (seed: StorySeed) => {
    const newSessionId = `sess_${Date.now()}`;
    setSessionId(newSessionId);
    setActiveSeed(seed);
    setStoryState({ tension: 0, trust: 0, progress: 0 });
    setEmotionState({ affection: initialAffection, tension: 0.3, trust: 0.4, comfort: 0.5 });
    setMessages([
      {
        id: `intro_${Date.now()}_narr`,
        role: 'narrator',
        content: `【${seed.title}】\n\n${seed.world_seed || seed.description || '故事开始了。'}`,
      },
    ]);
    setChoices([]);
    setInput('');
    setArcInfo(null);
    setStage('story');
    fetch(SESSIONS_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: newSessionId,
        title: seed.title || '未命名剧本',
        seed: { id: seed.id, title: seed.title, description: seed.description, world_seed: seed.world_seed },
        nsfw_level: nsfwLevel,
        initial_affection: initialAffection,
      }),
    }).catch(() => {});
  };

  const resumeSession = async (saved: SavedSession) => {
    try {
      const response = await fetch(`${SESSIONS_ENDPOINT}/${encodeURIComponent(saved.session_id)}`);
      if (!response.ok) return;
      const data = await response.json();

      setSessionId(saved.session_id);
      const seed = saved.seed as StorySeed | null;
      setActiveSeed(seed);
      setNsfwLevel((saved.nsfw_level || 'mild') as NsfwLevel);

      const restoredMessages: Message[] = [];
      const history: Array<{ user_input: string; narrative: string; protagonist_action: string; choices: StoryChoice[] }> = data.history || [];
      if (seed) {
        restoredMessages.push({
          id: 'intro_restored',
          role: 'narrator',
          content: `【${seed.title}】\n\n${seed.world_seed || seed.description || '故事开始了。'}`,
        });
      }
      for (let i = 0; i < history.length; i++) {
        const turn = history[i];
        restoredMessages.push({ id: `u_${i}`, role: 'user', content: turn.user_input });
        const fullNarrative = [turn.narrative, turn.protagonist_action].filter(Boolean).join('\n\n');
        restoredMessages.push({ id: `n_${i}`, role: 'narrator', content: fullNarrative });
      }

      setMessages(restoredMessages.length > 0 ? restoredMessages : DEFAULT_MESSAGES);

      const lastTurn = history[history.length - 1];
      setChoices(lastTurn ? (lastTurn.choices || []) : []);
      setStoryState({
        tension: data.state?.tension ?? 0,
        trust: data.state?.trust ?? 0,
        progress: data.state?.progress ?? 0,
      });
      setInput('');
      setArcInfo(null);
      setStage('story');

      // Load arc info
      try {
        const arcsResp = await fetch(`${SESSIONS_ENDPOINT}/${encodeURIComponent(saved.session_id)}/arcs`);
        if (arcsResp.ok) {
          const arcsData = await arcsResp.json();
          const arcs: Array<{ arc_number: number; title: string; status: string }> = arcsData.arcs || [];
          const active = arcs.find(a => a.status === 'active');
          const last = arcs[arcs.length - 1];
          const target = active || last;
          if (target) {
            setArcInfo({
              arc_number: target.arc_number,
              arc_title: target.title,
              arc_status: target.status,
              suggest_completion: false,
            });
          }
        }
      } catch {}
    } catch (error) {
      console.error('Failed to resume session', error);
    }
  };

  const deleteSession = async (sessionId: string) => {
    try {
      await fetch(`${SESSIONS_ENDPOINT}/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
      setSavedSessions(prev => prev.filter(s => s.session_id !== sessionId));
    } catch (error) {
      console.error('Failed to delete session', error);
    }
  };

  const toggleTag = (tagId: string) => {
    setSelectedTags(prev =>
      prev.includes(tagId) ? prev.filter(t => t !== tagId) : prev.length < 4 ? [...prev, tagId] : prev
    );
  };

  const generateSeeds = async () => {
    setIsGenerating(true);
    setCandidateSeeds([]);
    try {
      const response = await fetch(STORY_SEEDS_GENERATE_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tags: selectedTags,
          nsfw_level: nsfwLevel,
          count: 3,
          user_hint: userHint.trim(),
          randomize_personality: randomPersonality,
        }),
      });
      if (!response.ok) {
        console.error('Seed generation failed');
        return;
      }
      const data = await response.json();
      if (Array.isArray(data?.seeds)) {
        setCandidateSeeds(data.seeds);
      }
    } catch (error) {
      console.error('Failed to generate seeds', error);
    } finally {
      setIsGenerating(false);
    }
  };

  const backToLobby = async () => {
    setStage('lobby');
    setIsTyping(false);
    setChoices([]);
    setMessages(DEFAULT_MESSAGES);
    setInput('');
    setCandidateSeeds([]);
    setArcInfo(null);
    // Reload session list
    try {
      const response = await fetch(SESSIONS_ENDPOINT);
      if (response.ok) {
        const data = await response.json();
        if (Array.isArray(data?.sessions)) {
          setSavedSessions(data.sessions);
        }
      }
    } catch {}
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 150)}px`;
    }
  };

  const sendTurn = async (content: string, selectedChoiceId?: string) => {
    const trimmed = content.trim();
    if (!trimmed) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: trimmed,
    };

    setMessages(prev => [...prev, userMessage]);
    setChoices([]);
    setInput('');
    setIsTyping(true);

    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }

    const abortController = new AbortController();
    abortRef.current = abortController;

    try {
      const history = messages.map(msg => ({ role: msg.role, content: msg.content }));
      history.push({ role: 'user', content: trimmed });

      const payload = {
        session_id: sessionId,
        user_input: trimmed,
        selected_choice_id: selectedChoiceId || null,
        seed_id: activeSeed && activeSeed.id !== 'custom_seed' ? activeSeed.id : null,
        world_seed: activeSeed && activeSeed.id === 'custom_seed' ? activeSeed.world_seed : null,
        active_characters: activeCharacters,
        history,
      };

      const response = await fetch(STORY_STREAM_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: abortRef.current?.signal,
      });

      if (!response.ok || !response.body) {
        const fallback = await fetch(STORY_TURN_ENDPOINT, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!fallback.ok) throw new Error('Network response was not ok');
        const data = await fallback.json();

        const fullNarrative = [data?.narrative, data?.protagonist_action].filter(Boolean).join('\n\n');
        const narrativeMessage: Message = {
          id: `${Date.now()}_narrative`,
          role: 'narrator',
          content: fullNarrative || '夜色又深了一层，但事情似乎还没结束。',
        };

        setMessages(prev => [...prev, narrativeMessage]);
        setChoices(Array.isArray(data?.choices) ? data.choices : []);
        if (data?.current_state) {
          setStoryState({
            tension: data.current_state.tension ?? 0,
            trust: data.current_state.trust ?? 0,
            progress: data.current_state.progress ?? 0,
          });
        }
        if (Array.isArray(data?.active_characters) && data.active_characters.length > 0) {
          setActiveCharacters(data.active_characters);
        }
        if (data?.arc_info) {
          setArcInfo({
            arc_number: data.arc_info.arc_number ?? 1,
            arc_title: data.arc_info.arc_title ?? '',
            arc_status: data.arc_info.arc_status ?? 'active',
            suggest_completion: data.arc_info.suggest_completion ?? false,
          });
        }
        return;
      }

      const narrativeMessageId = `${Date.now()}_narrative`;
      const streamState: StoryStreamState = {
        narrative: '',
        protagonistAction: '',
        choices: [],
      };

      setMessages(prev => [
        ...prev,
        {
          id: narrativeMessageId,
          role: 'narrator',
          content: '...'
        }
      ]);

      const decoder = new TextDecoder();
      const reader = response.body.getReader();
      let pending = '';

      const applyNarrative = (text: string) => {
        setMessages(prev => prev.map(msg => (
          msg.id === narrativeMessageId ? { ...msg, content: text || '...' } : msg
        )));
      };

      const handleEvent = (eventName: string, dataRaw: string) => {
        if (!dataRaw) return;
        try {
          const payloadData = JSON.parse(dataRaw);
          if (eventName === 'narrative_chunk') {
            streamState.narrative += String(payloadData.chunk || '');
            applyNarrative(streamState.narrative);
            return;
          }
          if (eventName === 'protagonist_action') {
            streamState.protagonistAction = String(payloadData.content || '');
            return;
          }
          if (eventName === 'choices') {
            streamState.choices = Array.isArray(payloadData.choices) ? payloadData.choices : [];
            return;
          }
          if (eventName === 'active_characters') {
            const items = payloadData.active_characters;
            if (Array.isArray(items) && items.length > 0) {
              setActiveCharacters(items.map(String).slice(0, 3));
            }
            return;
          }
          if (eventName === 'current_state') {
            const cs = payloadData.current_state;
            if (cs) {
              setStoryState({
                tension: cs.tension ?? 0,
                trust: cs.trust ?? 0,
                progress: cs.progress ?? 0,
              });
            }
          }
          if (eventName === 'emotion') {
            const em = payloadData.emotion;
            if (em) {
              setEmotionState({
                affection: em.affection ?? 0.3,
                tension: em.tension ?? 0.3,
                trust: em.trust ?? 0.4,
                comfort: em.comfort ?? 0.5,
              });
            }
          }
          if (eventName === 'arc_info') {
            const ai = payloadData.arc_info;
            if (ai) {
              setArcInfo({
                arc_number: ai.arc_number ?? 1,
                arc_title: ai.arc_title ?? '',
                arc_status: ai.arc_status ?? 'active',
                suggest_completion: ai.suggest_completion ?? false,
              });
            }
          }
        } catch (err) {
          console.warn('Failed to parse SSE payload', err);
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });

        const blocks = pending.split('\n\n');
        pending = blocks.pop() || '';

        for (const block of blocks) {
          const lines = block.split('\n');
          let eventName = '';
          let dataLine = '';
          for (const line of lines) {
            if (line.startsWith('event:')) {
              eventName = line.replace('event:', '').trim();
            }
            if (line.startsWith('data:')) {
              dataLine += line.replace('data:', '').trim();
            }
          }
          if (eventName === 'done') {
            continue;
          }
          handleEvent(eventName, dataLine);
        }
      }

      const protagonistContent = streamState.protagonistAction;
      if (protagonistContent && !streamState.narrative.includes(protagonistContent)) {
        const merged = streamState.narrative + '\n\n' + protagonistContent;
        applyNarrative(merged);
      }

      setChoices(streamState.choices);
    } catch (error) {
      if (abortRef.current?.signal.aborted) {
        // User cancelled — not an error
      } else {
        console.error('Failed to fetch response:', error);
        const errorMessage: Message = {
          id: (Date.now() + 1).toString(),
          role: 'narrator',
          content: '风从窗缝灌进来，剧情在这里卡住了。请确认后端服务和模型服务正在运行。',
        };
        setMessages(prev => [...prev, errorMessage]);
      }
    } finally {
      abortRef.current = null;
      setIsTyping(false);
    }
  };

  const sendMessage = async () => {
    await sendTurn(input);
  };

  const handleChoiceClick = async (choice: StoryChoice) => {
    await sendTurn(choice.title, choice.id);
  };

  const stopGeneration = () => {
    abortRef.current?.abort();
  };

  const completeArc = async () => {
    setArcCompleting(true);
    try {
      const resp = await fetch(`${SESSIONS_ENDPOINT}/${encodeURIComponent(sessionId)}/arcs/complete`, { method: 'POST' });
      if (resp.ok) {
        const data = await resp.json();
        const arc = data.arc;
        setArcInfo({
          arc_number: arc.arc_number,
          arc_title: arc.title || '',
          arc_status: 'completed',
          suggest_completion: false,
        });
        setMessages(prev => [...prev, {
          id: `arc_complete_${Date.now()}`,
          role: 'narrator' as const,
          content: `\n——— 第${arc.arc_number}篇「${arc.title || ''}」完结 ———\n\n${arc.summary || '篇章已完结。'}`,
        }]);
        setChoices([]);
      }
    } catch (error) {
      console.error('Failed to complete arc', error);
    } finally {
      setArcCompleting(false);
    }
  };

  const startNewArc = async () => {
    setNewArcGenerating(true);
    try {
      const resp = await fetch(`${SESSIONS_ENDPOINT}/${encodeURIComponent(sessionId)}/arcs/new`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_hint: '', nsfw_level: nsfwLevel }),
      });
      if (resp.ok) {
        const data = await resp.json();
        const arc = data.arc;
        setArcInfo({
          arc_number: arc.arc_number,
          arc_title: arc.title || '',
          arc_status: 'active',
          suggest_completion: false,
        });
        setStoryState(prev => ({ ...prev, progress: 0 }));
        setMessages(prev => [...prev, {
          id: `arc_new_${Date.now()}`,
          role: 'narrator' as const,
          content: `\n——— 第${arc.arc_number}篇「${arc.title || ''}」开始 ———\n\n${data.world_seed || '新的篇章开始了。'}`,
        }]);
        setChoices([]);
      }
    } catch (error) {
      console.error('Failed to start new arc', error);
    } finally {
      setNewArcGenerating(false);
    }
  };

  // ── Snapshot functions (5.1) ───────────────────────────

  const loadSnapshots = async () => {
    try {
      const resp = await fetch(snapshotsEndpoint(sessionId));
      if (resp.ok) {
        const data = await resp.json();
        setSnapshots(Array.isArray(data?.snapshots) ? data.snapshots : []);
      }
    } catch (error) {
      console.warn('Failed to load snapshots', error);
    }
  };

  const createSnapshot = async () => {
    setSnapshotSaving(true);
    try {
      const resp = await fetch(snapshotsEndpoint(sessionId), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: '' }),
      });
      if (resp.ok) {
        await loadSnapshots();
      }
    } catch (error) {
      console.error('Failed to create snapshot', error);
    } finally {
      setSnapshotSaving(false);
    }
  };

  const restoreSnapshotAction = async (snap: Snapshot) => {
    try {
      const resp = await fetch(restoreSnapshotEndpoint(sessionId, snap.id), { method: 'POST' });
      if (!resp.ok) return;

      // Reload session state from server
      const detailResp = await fetch(`${SESSIONS_ENDPOINT}/${encodeURIComponent(sessionId)}`);
      if (!detailResp.ok) return;
      const data = await detailResp.json();

      const seed = activeSeed;
      const restoredMessages: Message[] = [];
      const history: Array<{ user_input: string; narrative: string; protagonist_action: string; choices: StoryChoice[] }> = data.history || [];

      if (seed) {
        restoredMessages.push({
          id: 'intro_restored',
          role: 'narrator',
          content: `【${seed.title}】\n\n${seed.world_seed || seed.description || '故事开始了。'}`,
        });
      }
      for (let i = 0; i < history.length; i++) {
        const turn = history[i];
        restoredMessages.push({ id: `u_${i}`, role: 'user', content: turn.user_input });
        const fullNarrative = [turn.narrative, turn.protagonist_action].filter(Boolean).join('\n\n');
        restoredMessages.push({ id: `n_${i}`, role: 'narrator', content: fullNarrative });
      }

      setMessages(restoredMessages.length > 0 ? restoredMessages : DEFAULT_MESSAGES);
      const lastTurn = history[history.length - 1];
      setChoices(lastTurn ? (lastTurn.choices || []) : []);
      setStoryState({
        tension: data.state?.tension ?? 0,
        trust: data.state?.trust ?? 0,
        progress: data.state?.progress ?? 0,
      });

      // Add a restored notice
      setMessages(prev => [...prev, {
        id: `restore_${Date.now()}`,
        role: 'narrator' as const,
        content: `\n🔄 已回溯到存档点「${snap.label}」(第${snap.turn_number}回)`,
      }]);
      setShowSnapshots(false);
      await loadSnapshots();
    } catch (error) {
      console.error('Failed to restore snapshot', error);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  if (stage === 'lobby') {
    return (
      <div className="app-container lobby-container">
        <header className="app-header">
          <div className="persona-info">
            <div className="persona-avatar"></div>
            <div className="persona-details">
              <h2>剧本工坊</h2>
              <p><span className="status-indicator online"></span> 选择标签 → AI 生成剧本 → 开始故事</p>
            </div>
          </div>
        </header>

        <div className="lobby-content">
          <section className="lobby-section">
            <h3>① 选择题材标签 <span className="tag-count-hint">（最多 4 个）</span></h3>
            <div className="theme-tag-grid">
              {themeTags.map((tag) => (
                <button
                  key={tag.id}
                  type="button"
                  className={`theme-tag-chip ${selectedTags.includes(tag.id) ? 'active' : ''}`}
                  onClick={() => toggleTag(tag.id)}
                >
                  <span className="theme-tag-icon">{tag.icon}</span>
                  <span className="theme-tag-label">{tag.label}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="lobby-section">
            <h3>② 选择尺度档位</h3>
            <div className="nsfw-selector">
              {(Object.keys(NSFW_LABELS) as NsfwLevel[]).map((lvl) => (
                <button
                  key={lvl}
                  type="button"
                  className={`nsfw-chip ${nsfwLevel === lvl ? 'active' : ''}`}
                  onClick={() => setNsfwLevel(lvl)}
                >
                  {NSFW_LABELS[lvl]}
                </button>
              ))}
            </div>
          </section>

          <section className="lobby-section">
            <h3>③ 补充要求 <span className="tag-count-hint">（可选）</span></h3>
            <textarea
              className="custom-textarea user-hint-input"
              value={userHint}
              onChange={(e) => setUserHint(e.target.value)}
              placeholder="描述你想要的剧情方向、角色关系、特定场景等，例如：希望有雨夜告白的桥段、女主先动心..."
              rows={3}
            />
          </section>

          <section className="lobby-section">
            <h3>④ 初始设定</h3>
            <div className="initial-settings">
              <div className="setting-row">
                <label className="setting-label">❤️ 初始好感度</label>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={Math.round(initialAffection * 100)}
                  onChange={(e) => setInitialAffection(Number(e.target.value) / 100)}
                  className="affection-slider"
                />
                <span className="setting-value">{Math.round(initialAffection * 100)}%</span>
              </div>
              <div className="setting-row">
                <label className="setting-label">🎲 随机生成女主性格</label>
                <button
                  type="button"
                  className={`toggle-chip ${randomPersonality ? 'active' : ''}`}
                  onClick={() => setRandomPersonality(!randomPersonality)}
                >
                  {randomPersonality ? '开启' : '关闭'}
                </button>
              </div>
            </div>
          </section>

          <section className="lobby-section">
            <h3>⑤ 生成剧本</h3>
            <button
              className="start-story-button ai-generate-button full-width"
              onClick={generateSeeds}
              type="button"
              disabled={isGenerating}
            >
              {isGenerating ? '正在构思剧本...' : 'AI 生成剧本方案'}
            </button>

            {isGenerating && (
              <div className="generating-hint">
                <div className="typing-indicator">
                  <div className="typing-dot"></div>
                  <div className="typing-dot"></div>
                  <div className="typing-dot"></div>
                </div>
                <span>AI 正在根据你选择的标签构思多个剧本...</span>
              </div>
            )}

            {candidateSeeds.length > 0 && (
              <div className="candidate-list">
                {candidateSeeds.map((seed) => (
                  <div key={seed.id} className="candidate-card">
                    <div className="candidate-title">{seed.title}</div>
                    {seed.personality && <div className="candidate-personality">🎭 {seed.personality}</div>}
                    <div className="candidate-desc">{seed.description}</div>
                    <div className="candidate-world">{seed.world_seed}</div>
                    <button
                      className="start-story-button"
                      onClick={() => startFromSeed(seed)}
                      type="button"
                    >
                      选择此剧本
                    </button>
                  </div>
                ))}
                <button
                  className="start-story-button regenerate-button"
                  onClick={generateSeeds}
                  type="button"
                  disabled={isGenerating}
                >
                  🔄 不满意？重新生成
                </button>
              </div>
            )}
          </section>

          {savedSessions.length > 0 && (
            <section className="lobby-section">
              <h3>📂 存档记录</h3>
              <div className="session-list">
                {savedSessions.map((s) => (
                  <div key={s.session_id} className="session-card">
                    <div className="session-info">
                      <div className="session-title">{s.title}</div>
                      <div className="session-meta">
                        第 {s.turn_count} 回 · {s.nsfw_level} · {new Date(s.updated_at).toLocaleDateString()}
                      </div>
                    </div>
                    <div className="session-actions">
                      <button className="session-resume-btn" onClick={() => resumeSession(s)} type="button">继续</button>
                      <button className="session-delete-btn" onClick={() => deleteSession(s.session_id)} type="button">删除</button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="lobby-section lobby-footer-hint">
            <p>你是Galgame男主角，林夕是女主角。信任与情绪会隐式影响剧情走向。</p>
          </section>
        </div>
      </div>
    );
  }

  return (
    <div className="app-container">
      <header className="app-header">
        <div className="persona-info">
          <div className="persona-avatar"></div>
          <div className="persona-details">
            <h2>{activeSeed?.title || '互动剧本'}{arcInfo ? ` · 第${arcInfo.arc_number}篇` : ''}</h2>
            <p><span className="status-indicator online"></span> 你 + 林夕{arcInfo?.arc_title ? ` — ${arcInfo.arc_title}` : ''}</p>
          </div>
        </div>
        <div className="header-actions">
          <button
            className="dashboard-toggle-button"
            onClick={() => setShowDashboard(prev => !prev)}
            type="button"
            title="显示/隐藏状态面板"
          >
            {showDashboard ? '📊 隐藏面板' : '📊 状态'}
          </button>
          <button
            className="dashboard-toggle-button"
            onClick={() => { setShowSnapshots(prev => !prev); if (!showSnapshots) { void loadSnapshots(); }}}
            type="button"
            title="存档/回溯"
          >
            📸 存档
          </button>
          <button className="back-lobby-button" onClick={backToLobby} type="button">返回剧本页</button>
        </div>
      </header>

      {showDashboard && (
        <>
          <div className="state-dashboard">
            <div className="state-bar">
              <span className="state-label">🎭 张力</span>
              <div className="state-track"><div className="state-fill tension" style={{ width: `${Math.max(0, (storyState.tension + 1) / 2 * 100)}%` }} /></div>
            </div>
            <div className="state-bar">
              <span className="state-label">💕 信任</span>
              <div className="state-track"><div className="state-fill trust" style={{ width: `${Math.max(0, (storyState.trust + 1) / 2 * 100)}%` }} /></div>
            </div>
            <div className="state-bar">
              <span className="state-label">📖 进度</span>
              <div className="state-track"><div className="state-fill progress" style={{ width: `${storyState.progress * 100}%` }} /></div>
            </div>
          </div>

          <div className="state-dashboard emotion-dashboard">
            <div className="state-bar">
              <span className="state-label">❤️ 好感</span>
              <div className="state-track"><div className="state-fill affection" style={{ width: `${emotionState.affection * 100}%` }} /></div>
            </div>
            <div className="state-bar">
              <span className="state-label">😰 紧张</span>
              <div className="state-track"><div className="state-fill emo-tension" style={{ width: `${emotionState.tension * 100}%` }} /></div>
            </div>
            <div className="state-bar">
              <span className="state-label">🤝 信赖</span>
              <div className="state-track"><div className="state-fill emo-trust" style={{ width: `${emotionState.trust * 100}%` }} /></div>
            </div>
            <div className="state-bar">
              <span className="state-label">☺️ 舒适</span>
              <div className="state-track"><div className="state-fill comfort" style={{ width: `${emotionState.comfort * 100}%` }} /></div>
            </div>
          </div>
        </>
      )}

      {showSnapshots && (
        <div className="state-dashboard snapshot-panel">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
            <span style={{ fontWeight: 600, fontSize: '0.9em' }}>📸 存档点</span>
            <button
              className="choice-button"
              onClick={createSnapshot}
              disabled={snapshotSaving || isTyping}
              style={{ padding: '4px 12px', fontSize: '0.85em' }}
            >
              {snapshotSaving ? '保存中...' : '💾 创建存档'}
            </button>
          </div>
          {snapshots.length === 0 ? (
            <p style={{ opacity: 0.5, fontSize: '0.85em', margin: '8px 0' }}>暂无存档</p>
          ) : (
            <div style={{ maxHeight: '180px', overflowY: 'auto' }}>
              {snapshots.map((snap) => (
                <div key={snap.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
                  <span style={{ fontSize: '0.85em' }}>
                    {snap.label} · {new Date(snap.created_at).toLocaleString()}
                  </span>
                  <button
                    className="session-resume-btn"
                    onClick={() => restoreSnapshotAction(snap)}
                    type="button"
                    style={{ fontSize: '0.8em', padding: '2px 8px' }}
                  >
                    回溯
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="chat-container">
        {messages.map((msg) => (
          <div key={msg.id} className={`message-wrapper ${msg.role}`}>
            <div className="message-bubble">{msg.content}</div>
          </div>
        ))}

        {isTyping && (
          <div className="message-wrapper narrator">
            <div className="message-bubble">
              <div className="typing-indicator">
                <div className="typing-dot"></div>
                <div className="typing-dot"></div>
                <div className="typing-dot"></div>
              </div>
            </div>
          </div>
        )}

        {!isTyping && choices.length > 0 && (
          <div className="choice-panel">
            <div className="choice-title">你想怎么推进剧情？</div>
            <div className="choice-list">
              {choices.map((choice) => (
                <button
                  key={choice.id}
                  className="choice-button"
                  onClick={() => handleChoiceClick(choice)}
                >
                  <span className="choice-main">{choice.title}</span>
                  {choice.description ? <span className="choice-desc">{choice.description}</span> : null}
                </button>
              ))}
            </div>
          </div>
        )}

        {!isTyping && arcInfo?.suggest_completion && arcInfo.arc_status === 'active' && (
          <div className="choice-panel arc-panel">
            <div className="choice-title">📖 篇章进展</div>
            <p style={{ margin: '8px 0', opacity: 0.8, fontSize: '0.9em' }}>
              当前篇章已发展到一个阶段，你可以选择完结后开始新的故事弧，或继续当前剧情。
            </p>
            <button
              className="choice-button"
              onClick={completeArc}
              disabled={arcCompleting}
              style={{ borderColor: '#e0a060' }}
            >
              <span className="choice-main">{arcCompleting ? '正在生成篇章总结...' : '✦ 完结当前篇章'}</span>
              <span className="choice-desc">AI 将总结本篇，之后你可以开启新篇章</span>
            </button>
          </div>
        )}

        {!isTyping && arcInfo?.arc_status === 'completed' && (
          <div className="choice-panel arc-panel">
            <div className="choice-title">🎉 篇章已完结</div>
            <p style={{ margin: '8px 0', opacity: 0.8, fontSize: '0.9em' }}>
              第{arcInfo.arc_number}篇「{arcInfo.arc_title}」已完结。你可以在同一世界和人物关系下开启新篇章。
            </p>
            <button
              className="choice-button"
              onClick={startNewArc}
              disabled={newArcGenerating}
              style={{ borderColor: '#60c0e0' }}
            >
              <span className="choice-main">{newArcGenerating ? '正在构思新篇章...' : '🌟 开启新篇章'}</span>
              <span className="choice-desc">延续现有人物关系和世界观，开始新的剧情弧</span>
            </button>
            <button className="choice-button" onClick={backToLobby}>
              <span className="choice-main">返回剧本页</span>
            </button>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <div className="input-area">
        <div className="input-box">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder="写下你的推进指令，Enter 发送..."
            rows={1}
          />
          {isTyping ? (
            <button
              className="stop-button"
              onClick={stopGeneration}
              type="button"
              title="终止输出"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              className="send-button"
              onClick={sendMessage}
              disabled={!input.trim() || isTyping}
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M22 2L11 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M22 2L15 22L11 13L2 9L22 2Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;
