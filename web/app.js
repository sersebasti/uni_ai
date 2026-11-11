const form = document.querySelector("#chat-form");
const questionInput = document.querySelector("#question");
const conversation = document.querySelector("#conversation");
const submitButton = document.querySelector("#submit");
const imageInput = document.querySelector("#image");
const attachmentName = document.querySelector("#attachment-name");
const clearImageButton = document.querySelector("#clear-image");
const startupPanel = document.querySelector("#startup-status");
const startupHeading = document.querySelector("#startup-heading");
const startupSummary = document.querySelector("#startup-summary");
const startupStages = document.querySelector("#startup-stages");
const startupError = document.querySelector("#startup-error");
let systemReady = false;
let chatBusy = false;

function updateSubmitButton() {
  submitButton.disabled = !systemReady || chatBusy;
  submitButton.textContent = chatBusy ? "Attendi" : systemReady ? "Invia" : "Non pronto";
}

function renderStartup(state) {
  systemReady = state.ready;
  updateSubmitButton();
  startupPanel.dataset.status = state.status;
  startupPanel.setAttribute("aria-busy", String(state.status === "preparing"));
  startupHeading.textContent = state.ready
    ? "Sistema pronto"
    : state.status === "error" ? "Preparazione non riuscita" : "Preparazione del sistema";
  startupSummary.textContent = state.ready
    ? "Puoi iniziare a fare domande sulle dispense."
    : state.status === "error"
      ? "Correggi il problema indicato e riavvia il servizio web."
      : "Le attivita vengono eseguite in background. Puoi lasciare aperta questa pagina.";
  startupError.hidden = !state.error;
  startupError.textContent = state.error || "";
  const labels = { pending: "In attesa", running: "In corso", done: "Completato", error: "Errore" };
  const stages = state.stages.map((stage) => {
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
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`Stato non disponibile (HTTP ${response.status}).`);
    renderStartup(await response.json());
  } catch (error) {
    systemReady = false;
    updateSubmitButton();
    startupPanel.setAttribute("aria-busy", "true");
    startupHeading.textContent = "Connessione al server interrotta";
    startupSummary.textContent = "Nuovo tentativo automatico tra pochi secondi.";
    startupError.hidden = false;
    startupError.textContent = error.message;
  } finally {
    window.setTimeout(pollStartup, systemReady ? 10000 : 1000);
  }
}

pollStartup();

function addMessage(content, role, sources = [], question = "") {
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
    illustrationButton.addEventListener("click", async () => {
      illustrationButton.disabled = true;
      illustrationButton.textContent = "Generazione";
      try {
        const response = await fetch("/api/illustrations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, answer: content }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Errore durante la generazione.");
        const image = document.createElement("img");
        image.className = "generated-image";
        image.src = data.image_url;
        image.alt = "Illustrazione generata della risposta";
        message.append(image);
        actions.remove();
      } catch (error) {
        illustrationButton.disabled = false;
        illustrationButton.textContent = "Riprova immagine";
      }
    });
    actions.append(illustrationButton);
    message.append(actions);
  }

  conversation.append(message);
  message.scrollIntoView({ behavior: "smooth", block: "end" });
}

function clearAttachment() {
  imageInput.value = "";
  attachmentName.textContent = "";
  clearImageButton.hidden = true;
}

imageInput.addEventListener("change", () => {
  const [file] = imageInput.files;
  attachmentName.textContent = file ? file.name : "";
  clearImageButton.hidden = !file;
});

clearImageButton.addEventListener("click", clearAttachment);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question || !systemReady || chatBusy) return;

  const formData = new FormData();
  formData.append("question", question);
  if (imageInput.files[0]) formData.append("image", imageInput.files[0]);

  addMessage(`${question}${imageInput.files[0] ? "\n[Immagine allegata]" : ""}`, "user");
  questionInput.value = "";
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
    addMessage(data.answer, "assistant", data.sources, question);
  } catch (error) {
    addMessage(error.message, "error");
  } finally {
    chatBusy = false;
    updateSubmitButton();
    questionInput.focus();
  }
});