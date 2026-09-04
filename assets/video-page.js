(() => {
  const video = document.querySelector("video");
  const transcriptElement = document.getElementById("timed-text-data");
  const proofElement = document.getElementById("proof-data");
  const input = document.getElementById("transcript-search");
  const results = document.getElementById("search-results");
  const summary = document.getElementById("search-summary");
  if (!video || !transcriptElement || !proofElement || !input || !results || !summary) return;

  let transcript = { entries: [] };
  let proof = { concepts: [] };
  try { transcript = JSON.parse(transcriptElement.textContent || "{}"); } catch (_) {}
  try { proof = JSON.parse(proofElement.textContent || "{}"); } catch (_) {}

  const languageOrder = [
    ["ja", "JP"],
    ["en", "EN"],
    ["zh", "ZH"],
  ];
  const entries = Array.isArray(transcript.entries) ? transcript.entries : [];
  const rows = [];
  for (const entry of entries) {
    const tracks = entry && typeof entry.tracks === "object" ? entry.tracks : {};
    for (const [language, label] of languageOrder) {
      const track = tracks[language];
      if (!track || typeof track.text !== "string") continue;
      rows.push({
        language,
        label,
        text: track.text,
        start: entry.start || "",
        startSeconds: Number(entry.start_seconds),
      });
    }
  }

  function normalized(value) {
    return value.normalize("NFKC").toLocaleLowerCase();
  }

  function seekTo(seconds) {
    if (!Number.isFinite(seconds)) return;
    video.currentTime = Math.max(0, seconds);
    video.dispatchEvent(new Event("seeked"));
    video.scrollIntoView({ behavior: "smooth", block: "center" });
    video.focus({ preventScroll: true });
  }

  function renderResults() {
    const query = normalized(input.value.trim());
    results.replaceChildren();
    if (!query) {
      summary.textContent = `Search all ${rows.length} aligned transcript strings.`;
      return;
    }
    const matches = rows.filter((row) => normalized(row.text).includes(query));
    summary.textContent = `${matches.length} ${matches.length === 1 ? "match" : "matches"} across Japanese, English, and Chinese.`;
    for (const match of matches) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "search-result";
      button.setAttribute("aria-label", `${match.label} at ${match.start}: ${match.text}`);

      const language = document.createElement("span");
      language.className = "search-result-lang";
      language.textContent = match.label;
      const time = document.createElement("span");
      time.className = "search-result-time";
      time.textContent = match.start;
      const text = document.createElement("span");
      text.className = "search-result-text";
      text.textContent = match.text;
      button.append(language, time, text);
      button.addEventListener("click", () => seekTo(match.startSeconds));
      results.append(button);
    }
  }

  input.addEventListener("input", renderResults);
  for (const button of document.querySelectorAll(".concept-card[data-seek]")) {
    button.addEventListener("click", () => seekTo(Number(button.dataset.seek)));
  }

  const concepts = Array.isArray(proof.concepts) ? proof.concepts : [];
  if (concepts.length !== 5) {
    document.querySelector(".concept-grid")?.setAttribute("data-validation", "unexpected-concept-count");
  }
  renderResults();
})();
