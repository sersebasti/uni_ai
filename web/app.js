const form = document.querySelector("#chat-form");
const questionInput = document.querySelector("#question");
const conversations = {
  theory: document.querySelector("#conversation-theory"),
  practice: document.querySelector("#conversation-practice"),
};
const sectionButtons = document.querySelectorAll("[data-section]");
const sectionDescription = document.querySelector("#section-description");
const practiceSettings = document.querySelector("#practice-settings");
const cadVersion = document.querySelector("#cad-version");
const cadLanguage = document.querySelector("#cad-language");
const submitButton = document.querySelector("#submit");
const imageInput = document.querySelector("#image");
const attachmentName = document.querySelector("#attachment-name");
const clearImageButton = document.querySelector("#clear-image");
const startupPanel = document.querySelector("#startup-status");
const startupIndicator = document.querySelector("#startup-indicator");
const startupHeading = document.querySelector("#startup-heading");
const startupSummary = document.querySelector("#startup-summary");
const startupStages = document.querySelector("#startup-stages");
const startupError = document.querySelector("#startup-error");
let systemReady = false;
let chatBusy = false;
let selectedSection = "theory";
let statusTimer;
let statusGeneration = 0;
const drafts = { theory: "", practice: "" };
const attachments = { theory: null, practice: null };

function updateSubmitButton() {
  submitButton.disabled = !systemReady || chatBusy;
  submitButton.textContent = chatBusy ? "Attendi" : systemReady ? "Invia" : "Non pronto";
}

function renderStartup(state) {
  systemReady = state.ready;
  updateSubmitButton();
  startupPanel.dataset.status = state.status;
  startupIndicator.textContent = state.ready
    ? "Pronto"
    : state.status === "empty" ? "Nessun PDF"
      : state.status === "error" ? "Errore" : "In preparazione";
  startupPanel.setAttribute("aria-busy", String(state.status === "preparing"));
  startupHeading.textContent = state.ready
    ? "Sistema pronto"
    : state.status === "empty" ? "In attesa dei documenti"
      : state.status === "error" ? "Preparazione non riuscita" : "Preparazione del sistema";
  startupSummary.textContent = state.ready
    ? `Puoi iniziare a fare domande nella sezione ${state.label}.`
    : state.status === "empty"
      ? `Inserisci i PDF in ${state.documents_dir}/ e riavvia il servizio web. L'altra sezione resta indipendente.`
      : state.status === "error"
      ? "Correggi il problema indicato e riavvia il servizio web."
      : "Le attivita vengono eseguite in background. Puoi lasciare aperta questa pagina.";
  startupError.hidden = !state.error;
  startupError.textContent = state.error || "";
  const labels = { pending: "In attesa", running: "In corso", done: "Completato", error: "Errore" };
  const stages = (state.status === "empty" ? [] : state.stages).map((stage) => {
    const item = document.createElement("li");
    item.className = "startup-stage";
    item.dataset.status = stage.status;
    const title = document.createElement("strong");
    title.textContent = `${stage.label} - ${labels[stage.status]}`;
    const detail = document.createElement("span");
    detail.textContent = stage.detail;
    const progress = document.createElement("progress");
    progress.setAttribute("aria-label", stage.label);
    if (stage.status === "done") {
      progress.max = 1;
      progress.value = 1;
    } else if (stage.total > 0) {
      progress.max = stage.total;
      progress.value = stage.completed;
      const percent = Math.floor(stage.completed / stage.total * 100);
      detail.textContent += stage.id === "model"
        ? ` - ${percent}% del livello`
        : ` - ${stage.completed}/${stage.total} (${percent}%)`;
    } else if (stage.status !== "running") {
      progress.max = 1;
      progress.value = 0;
    }
    item.append(title, progress, detail);
    return item;
  });
  startupStages.replaceChildren(...stages);
}

async function pollStartup() {
  const section = selectedSection;
  const generation = statusGeneration;
  try {
    const response = await fetch(`/api/status?section=${section}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Stato non disponibile (HTTP ${response.status}).`);
    const state = await response.json();
    if (generation === statusGeneration) renderStartup(state);
  } catch (error) {
    if (generation !== statusGeneration) return;
    systemReady = false;
    updateSubmitButton();
    startupPanel.dataset.status = "error";
    startupIndicator.textContent = "Connessione interrotta";
    startupPanel.setAttribute("aria-busy", "true");
    startupHeading.textContent = "Connessione al server interrotta";
    startupSummary.textContent = "Nuovo tentativo automatico tra pochi secondi.";
    startupError.hidden = false;
    startupError.textContent = error.message;
  } finally {
    if (generation === statusGeneration) {
      statusTimer = window.setTimeout(pollStartup, systemReady ? 10000 : 1000);
    }
  }
}

for (const button of sectionButtons) {
  button.addEventListener("click", () => {
    const section = button.dataset.section;
    if (section === selectedSection) return;
    drafts[selectedSection] = questionInput.value;
    selectedSection = section;
    statusGeneration += 1;
    questionInput.value = drafts[section];
    for (const [name, view] of Object.entries(conversations)) view.hidden = name !== section;
    for (const tab of sectionButtons) {
      tab.setAttribute("aria-pressed", String(tab.dataset.section === section));
    }
    practiceSettings.hidden = section !== "practice";
    sectionDescription.textContent = section === "practice"
      ? "Procedure guidate dai tuoi manuali di Solid Edge, separate dalle dispense teoriche."
      : "Concetti e spiegazioni dalle dispense teoriche.";
    questionInput.placeholder = section === "practice"
      ? "Ad esempio: Come creo un foro filettato in Solid Edge?"
      : "Ad esempio: Spiegami il concetto di ...";
    renderAttachment();
    systemReady = false;
    updateSubmitButton();
    startupHeading.textContent = "Verifica della sezione";
    startupSummary.textContent = "Connessione al server in corso...";
    startupStages.replaceChildren();
    startupError.hidden = true;
    startupPanel.setAttribute("aria-busy", "true");
    startupPanel.dataset.status = "preparing";
    startupIndicator.textContent = "Verifica in corso...";
    window.clearTimeout(statusTimer);
    pollStartup();
  });
}

pollStartup();

function addMessage(content, role, sources = [], question = "", section = selectedSection) {
  const message = document.createElement("article");
  message.className = `message ${role}`;
  message.textContent = content;

  if (sources.length) {
    const sourceList = document.createElement("ul");
    sourceList.className = "sources";
    for (const source of sources) {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = source.document_url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = `${source.lecture}, pagina ${source.page}`;
      link.setAttribute("aria-label", `Apri la dispensa ${source.lecture}, pagina ${source.page}`);
      item.append(link);
      sourceList.append(item);
    }
    message.append(sourceList);
  }

  if (role === "assistant" && question) {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    const illustrationButton = document.createElement("button");
    illustrationButton.type = "button";
    illustrationButton.className = "illustration-button";
    illustrationButton.textContent = "Genera immagine";
    const illustrationError = document.createElement("p");
    illustrationError.className = "illustration-error";
    illustrationError.setAttribute("role", "alert");
    illustrationError.hidden = true;
    illustrationButton.addEventListener("click", async () => {
      illustrationButton.disabled = true;
      illustrationButton.textContent = "Generazione";
      illustrationError.hidden = true;
      illustrationError.textContent = "";
      try {
        const response = await fetch("/api/illustrations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, answer: content, section }),
        });
        if (!response.headers.get("content-type")?.includes("application/json")) {
          throw new Error(`Generazione non disponibile (HTTP ${response.status}). Riprova piu tardi.`);
        }
        const data = await response.json();
        if (!response.ok) {
          throw new Error(typeof data.detail === "string"
            ? data.detail
            : "Domanda o risposta non valida per la generazione. Prova con un testo piu breve.");
        }
        if (typeof data.image_url !== "string" || !data.image_url) {
          throw new Error("Il server non ha restituito l'indirizzo dell'immagine.");
        }
        const image = document.createElement("img");
        image.className = "generated-image";
        image.src = data.image_url;
        image.alt = "Illustrazione generata della risposta";
        message.append(image);
        actions.remove();
      } catch (error) {
        illustrationError.textContent = error.message;
        illustrationError.hidden = false;
        illustrationButton.disabled = false;
        illustrationButton.textContent = "Riprova immagine";
      }
    });
    actions.append(illustrationButton, illustrationError);
    message.append(actions);
  }

  conversations[section].append(message);
  if (section === selectedSection) message.scrollIntoView({ behavior: "smooth", block: "end" });
}

function renderAttachment() {
  imageInput.value = "";
  const file = attachments[selectedSection];
  attachmentName.textContent = file ? file.name : "";
  clearImageButton.hidden = !file;
}

function clearAttachment() {
  attachments[selectedSection] = null;
  renderAttachment();
}

imageInput.addEventListener("change", () => {
  const [file] = imageInput.files;
  attachments[selectedSection] = file || null;
  attachmentName.textContent = file ? file.name : "";
  clearImageButton.hidden = !file;
});

clearImageButton.addEventListener("click", clearAttachment);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question || !systemReady || chatBusy) return;
  const section = selectedSection;
  const attachment = attachments[section];

  const formData = new FormData();
  formData.append("question", question);
  formData.append("section", section);
  if (section === "practice") {
    formData.append("cad_version", cadVersion.value.trim());
    formData.append("cad_language", cadLanguage.value);
  }
  if (attachment) formData.append("image", attachment);

  addMessage(`${question}${attachment ? "\n[Immagine allegata]" : ""}`, "user", [], "", section);
  questionInput.value = "";
  drafts[section] = "";
  clearAttachment();
  chatBusy = true;
  updateSubmitButton();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Errore durante la richiesta.");
    addMessage(data.answer, "assistant", data.sources, question, section);
  } catch (error) {
    addMessage(error.message, "error", [], "", section);
  } finally {
    chatBusy = false;
    updateSubmitButton();
    if (section === selectedSection) questionInput.focus();
  }
});