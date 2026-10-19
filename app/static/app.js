const picker = document.querySelector("#file-input");
const dropzone = document.querySelector("#dropzone");
const errorMessage = document.querySelector("#error-message");
const jobPanel = document.querySelector("#job-panel");
const jobName = document.querySelector("#job-name");
const jobKicker = document.querySelector("#job-kicker");
const jobMessage = document.querySelector("#job-message");
const results = document.querySelector("#results");
const audioPlayer = document.querySelector("#audio-player");
let pollTimer;
let currentJob;

function showError(message) {
  errorMessage.textContent = message;
  errorMessage.hidden = false;
}

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = undefined;
}

async function reset() {
  stopPolling();
  audioPlayer.pause();
  audioPlayer.removeAttribute("src");
  audioPlayer.load();
  const previousJob = currentJob;
  currentJob = undefined;
  picker.value = "";
  errorMessage.hidden = true;
  results.hidden = true;
  jobPanel.hidden = true;
  dropzone.hidden = false;
  if (previousJob) {
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(previousJob)}`, { method: "DELETE" });
      if (!response.ok && response.status !== 404) {
        showError("This track could not be cleared from temporary storage.");
      }
    } catch {
      showError("Couldn't clear this track from temporary storage.");
    }
  }
}

async function chooseFile(file) {
  if (!file) return;
  reset();
  const formData = new FormData();
  formData.append("file", file);
  dropzone.hidden = true;
  jobPanel.hidden = false;
  jobPanel.dataset.status = "queued";
  document.querySelector("#reset-button").disabled = true;
  jobName.textContent = file.name.replace(/\.[^.]+$/, "") || "Your song";
  jobKicker.textContent = "GETTING THINGS READY";
  jobMessage.textContent = "Uploading your track to the local processor…";

  try {
    const response = await fetch("/api/jobs", { method: "POST", body: formData });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "We couldn't upload that track.");
    currentJob = data.id;
    jobName.textContent = data.name;
    pollJob();
  } catch (error) {
    jobPanel.hidden = true;
    dropzone.hidden = false;
    showError(error.message || "Couldn't reach the local processor. Is the app running?");
  }
}

async function pollJob() {
  if (!currentJob) return;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(currentJob)}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || "We couldn't check this track.");
    jobPanel.dataset.status = job.status;
    document.querySelector("#reset-button").disabled = ["queued", "processing"].includes(job.status);
    jobMessage.textContent = job.message;

    if (job.status === "completed") {
      jobKicker.textContent = "READY WHEN YOU ARE";
      results.hidden = false;
      for (const track of ["instrumental", "vocals"]) {
        const url = `/api/jobs/${encodeURIComponent(currentJob)}/tracks/${track}`;
        results.querySelector(`[data-track="${track}"]`).dataset.audioUrl = url;
        results.querySelector(`[data-download="${track}"]`).href = url;
      }
      stopPolling();
      return;
    }
    if (job.status === "failed") {
      jobKicker.textContent = "LET'S TRY THAT AGAIN";
      stopPolling();
      return;
    }
    pollTimer = setTimeout(pollJob, 1400);
  } catch (error) {
    jobPanel.dataset.status = "failed";
    document.querySelector("#reset-button").disabled = false;
    jobKicker.textContent = "SOMETHING WENT WRONG";
    jobMessage.textContent = error.message;
    stopPolling();
  }
}

picker.addEventListener("change", () => chooseFile(picker.files[0]));
document.querySelector("#reset-button").addEventListener("click", reset);

dropzone.addEventListener("keydown", event => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    picker.click();
  }
});

for (const eventName of ["dragenter", "dragover"]) {
  dropzone.addEventListener(eventName, event => {
    event.preventDefault();
    dropzone.classList.add("is-dragging");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  dropzone.addEventListener(eventName, event => {
    event.preventDefault();
    dropzone.classList.remove("is-dragging");
  });
}
dropzone.addEventListener("drop", event => chooseFile(event.dataTransfer.files[0]));

results.addEventListener("click", event => {
  const button = event.target.closest("[data-track]");
  if (!button) return;
  const url = button.dataset.audioUrl;
  if (!url) return;
  if (audioPlayer.src === new URL(url, location.href).href && !audioPlayer.paused) {
    audioPlayer.pause();
    button.textContent = "Play";
    return;
  }
  for (const playButton of results.querySelectorAll("[data-track]")) playButton.textContent = "Play";
  audioPlayer.src = url;
  audioPlayer.play()
    .then(() => { button.textContent = "Pause"; })
    .catch(() => showError("Couldn't play that track. Try downloading the WAV instead."));
  audioPlayer.onended = () => { button.textContent = "Play"; };
  audioPlayer.onpause = () => { button.textContent = "Play"; };
});
