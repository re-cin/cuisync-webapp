document.addEventListener('DOMContentLoaded', () => {
  // Elements
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('image-upload-input');
  const imageForm = document.getElementById('image-search-form');
  const originSelect = document.getElementById('img-origin');
  const originDisplay = document.getElementById('img-origin-display');
  const targetSelect = document.getElementById('img-target');
  const imageSearchButton = imageForm.querySelector('button[type="submit"]');
  const hiddenDishInput = document.getElementById('hidden-dish-name');
  const defaultState = document.getElementById('dropzone-default');
  const previewState = document.getElementById('dropzone-preview');
  const previewImg = document.getElementById('preview-img');
  const fileName = document.getElementById('file-name');
  const removeBtn = document.getElementById('remove-img-btn');
  const selectedDishCard = document.getElementById('selected-dish-card');
  const selectedDishName = document.getElementById('selected-dish-name');
  const selectedDishAltName = document.getElementById('selected-dish-alt-name');
  const selectedDishCountry = document.getElementById('selected-dish-country');
  const selectedDishImage = document.getElementById('selected-dish-image');
  const ocrLoading = document.getElementById('ocr-loading');
  const ocrNote = document.getElementById('ocr-loading-note');
  const picker = document.getElementById('dish-picker-overlay');
  const pickerList = document.getElementById('dish-picker-list');
  const pickerClose = document.getElementById('dish-picker-close');
  const selectDishPrompt = document.getElementById('select-dish-prompt');
  const selectDishPromptCount = document.getElementById('select-dish-prompt-count');

  // State
  let activeScanController = null;
  let detectedMatches = [];
  let isScanning = false;
  let ocrTimer = null;

  // Loading tips
  const OCR_NOTES = [
    'Note: Use a clear, readable photo.',
    'Note: Results depend on image quality and language.',
    'Note: Longer menus take more time.',
  ];

  // Show the country, or a placeholder
  function updateOriginDisplay() {
    const option = originSelect.options[originSelect.selectedIndex];
    const hasCountry = Boolean(originSelect.value && option);

    if (isScanning) {
      originDisplay.textContent = 'Finding Origin Country';
    } else if (hasCountry) {
      originDisplay.textContent = option.textContent;
    } else {
      originDisplay.textContent = 'Country of Origin';
    }

    originDisplay.classList.toggle('is-placeholder', isScanning || !hasCountry);
    originDisplay.classList.toggle('is-scanning', isScanning);
  }

  function setOrigin(country) {
    const option = [...originSelect.options].find(
      (item) => item.value && item.value.toLowerCase() === (country || '').toLowerCase()
    );
    originSelect.value = option ? option.value : '';
    updateOriginDisplay();
  }

  // The image search uses the same card popup as the text search (text-search.js)
  let usingCardPopup = false;

  // Show "choose a dish" card if picker was closed without picking
  function updateSelectDishPrompt() {
    const newPopup = document.getElementById('dish-results-overlay');
    const pickerOpen = !picker.classList.contains('hidden')
      || (newPopup && !newPopup.classList.contains('hidden') && usingCardPopup);
    const needsChoice = detectedMatches.length >= 2 && !hiddenDishInput.value;

    if (needsChoice && !pickerOpen) {
      selectDishPromptCount.textContent =
        `${detectedMatches.length} dishes found · Click to choose`;
      selectDishPrompt.style.display = 'flex';
    } else {
      selectDishPrompt.style.display = 'none';
    }
  }

  function closeDishPicker() {
    picker.classList.add('hidden');
    // No dish picked, so no country yet
    if (!hiddenDishInput.value) setOrigin('');
    updateSelectDishPrompt();
  }

  function stopOcrTimer() {
    if (ocrTimer) clearInterval(ocrTimer);
    ocrTimer = null;
  }

  // Swap tips every 4s
  function startOcrTimer() {
    stopOcrTimer();
    let noteIndex = 0;
    if (ocrNote) {
      ocrNote.textContent = OCR_NOTES[0];
      ocrNote.classList.remove('is-fading');
    }
    ocrTimer = setInterval(() => {
      if (ocrNote) {
        ocrNote.classList.add('is-fading');
        setTimeout(() => {
          noteIndex = (noteIndex + 1) % OCR_NOTES.length;
          ocrNote.textContent = OCR_NOTES[noteIndex];
          ocrNote.classList.remove('is-fading');
        }, 250);
      }
    }, 4000);
  }

  function setOcrLoading(isLoading) {
    if (isLoading) startOcrTimer();
    else stopOcrTimer();
    isScanning = isLoading;
    updateOriginDisplay();
    ocrLoading.style.display = isLoading ? 'block' : 'none';
    targetSelect.disabled = isLoading;
    imageSearchButton.disabled = isLoading;
    imageForm.classList.toggle('is-ocr-loading', isLoading);
  }

  // Fill the dish card
  function selectDish(match, showCard = true, canChange = true) {
    setOrigin(match.country);
    hiddenDishInput.value = match.title;
    selectedDishName.textContent = match.title;
    if (match.alternative_title) {
      selectedDishAltName.textContent = match.alternative_title;
      selectedDishAltName.style.display = '';
    } else {
      selectedDishAltName.textContent = '';
      selectedDishAltName.style.display = 'none';
    }
    selectedDishCountry.textContent = match.country;
    if (match.image_link) {
      selectedDishImage.src = match.image_link;
      selectedDishImage.alt = match.title;
      selectedDishImage.style.display = '';
    } else {
      selectedDishImage.removeAttribute('src');
      selectedDishImage.style.display = 'none';
    }
    selectedDishCard.style.display = showCard ? 'flex' : 'none';
    selectedDishCard.querySelector('.selected-dish-change').style.display = canChange ? 'inline' : 'none';
    selectedDishCard.setAttribute(
      'aria-label',
      canChange ? 'Change selected dish' : 'Selected dish'
    );
    if (usingCardPopup && window.CuiSyncPopup) {
      window.CuiSyncPopup.close();   // runs closeDishPicker() through the popup's onClose
    } else {
      closeDishPicker();
    }
  }

  // One match = auto pick, more = show list
  function showDishPicker(matches) {
    detectedMatches = matches;
    if (matches.length === 1) {
      selectDish(matches[0], true, false);
      return;
    }
    if (window.CuiSyncPopup) {
      const titleCase = (t) => String(t || '').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
      const cards = matches.map((m) => ({
        ...m,
        country_label: m.country_label || titleCase(m.country),
        country_value: (m.country_value || m.country || '').toLowerCase(),
        tags: m.tags || [],
        ingredients: m.ingredients || [],
        score: null,
        found: [],
      }));
      usingCardPopup = true;
      window.CuiSyncPopup.show(
        { mode: 'image', total: cards.length, dishes: cards },
        (card) => selectDish(matches[cards.indexOf(card)]),
        () => { usingCardPopup = false; closeDishPicker(); }
      );
      updateSelectDishPrompt();
      return;
    }
    pickerList.replaceChildren();

    matches.forEach((match) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'dish-picker-option';

      const infoDiv = document.createElement('div');
      infoDiv.className = 'dish-picker-info';

      const titleEl = document.createElement('strong');
      titleEl.textContent = match.title;
      infoDiv.appendChild(titleEl);

      if (match.alternative_title) {
        const altEl = document.createElement('em');
        altEl.className = 'dish-picker-alt-name';
        altEl.textContent = match.alternative_title;
        infoDiv.appendChild(altEl);
      }

      const countryEl = document.createElement('span');
      countryEl.className = 'dish-picker-country';
      countryEl.textContent = match.country;
      infoDiv.appendChild(countryEl);

      button.appendChild(infoDiv);

      const imgEl = document.createElement('img');
      imgEl.className = 'dish-picker-image';
      imgEl.alt = match.title;
      imgEl.loading = 'lazy';

      if (match.image_link) {
        imgEl.src = match.image_link;
      } else {
        imgEl.style.display = 'none';
      }

      // Hide broken images
      imgEl.addEventListener('error', () => {
        imgEl.style.display = 'none';
      });

      button.appendChild(imgEl);

      button.addEventListener('click', (e) => {
        e.stopPropagation();
        selectDish(match);
      });
      pickerList.appendChild(button);
    });

    picker.classList.remove('hidden');
    updateSelectDishPrompt();
  }

  // Send photo to the server
  async function extractImageInfo(file) {
    hiddenDishInput.value = '';
    detectedMatches = [];
    closeDishPicker();
    selectedDishCard.style.display = 'none';
    // Cancel the old scan
    if (activeScanController) activeScanController.abort();
    const scanController = new AbortController();
    activeScanController = scanController;
    const formData = new FormData();
    formData.append('dish_image', file);
    formData.append('origin_country', originSelect.value);
    setOcrLoading(true);

    try {
      const response = await fetch('/extract-dish-info', {
        method: 'POST',
        body: formData,
        signal: scanController.signal,
      });
      const data = await response.json();
      // Ignore old results
      if (activeScanController !== scanController) return;

      if (data.matches && data.matches.length) {
        showDishPicker(data.matches);
      } else if (data.error === 'unsupported_script') {
        // Khmer, Burmese or blurry
        showErrorModal('Language Not Supported or Blurry Image', data.message);
      } else if (data.error === 'too_large') {
        // Over the upload limit
        showErrorModal('Image Too Large', data.message);
      } else if (data.error === 'no_text') {
        // Nothing to read
        showErrorModal('No Text Detected', data.message);
      } else if (!data.raw_text && data.message) {
        // Server error, show why
        showErrorModal('Scanning Error', data.message);
      } else {
        // Text found, no dish
        showErrorModal(
          "We Can't Find Your Dish",
          "We couldn't find the dish in the recipe database. Try another dish or use a clearer photo."
        );
      }
    } catch (err) {
      if (err.name === 'AbortError') return;
      console.error('Error connecting to extraction service:', err);
      showErrorModal('Scanning Error', 'The menu could not be scanned. Please try the photo again.');
    } finally {
      if (activeScanController === scanController) {
        activeScanController = null;
        setOcrLoading(false);
      }
    }
  }

  // Show preview, then scan
  // Same 16 MB limit as app.py
  const MAX_UPLOAD_BYTES = 16 * 1024 * 1024;

  function displayPreview(file) {
    if (!file.type.startsWith('image/')) {
      showErrorModal('Unsupported File', 'Please upload an image file.');
      return;
    }
    // Check size here, Render cuts off big uploads before the server can answer
    if (file.size > MAX_UPLOAD_BYTES) {
      fileInput.value = '';
      showErrorModal('Image Too Large', 'This photo is too large. Please upload an image under 16 MB.');
      return;
    }
    setOrigin('');
    const reader = new FileReader();
    reader.onload = (event) => {
      previewImg.src = event.target.result;
      fileName.textContent = file.name;
      defaultState.style.display = 'none';
      previewState.style.display = 'flex';
    };
    reader.readAsDataURL(file);
    extractImageInfo(file);
  }

  // Phones and tablets: only the Upload / Take a Photo buttons open the picker, not the whole box
  const isTouchDevice = window.matchMedia('(hover: none), (pointer: coarse)').matches;

  // Open file picker, except on inner buttons
  dropzone.addEventListener('click', (event) => {
    if (isTouchDevice) return;
    if (
      event.target.closest('#remove-img-btn') ||
      event.target.closest('#selected-dish-card') ||
      event.target.closest('#select-dish-prompt')
    ) {
      return;
    }
    fileInput.click();
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files[0]) displayPreview(fileInput.files[0]);
  });

  // Take a Photo. Some Android browsers ignore capture="environment" on a file input and only
  // show the gallery, so the camera is opened inside the page (getUserMedia) when possible.
  // If the browser blocks that, the user can still open the phone's camera app or upload.
  const cameraInput = document.getElementById('camera-input');
  const cameraBtn = document.getElementById('camera-btn');
  const uploadBtn = document.getElementById('upload-photo-btn');

  // Same as choosing a file: put it in the upload field and scan it
  function useCapturedFile(file) {
    try {
      const transfer = new DataTransfer();
      transfer.items.add(file);
      fileInput.files = transfer.files;
    } catch (error) {
      console.warn('[Cuisync] Could not copy the camera photo into the upload field', error);
    }
    displayPreview(file);
  }

  let cameraStream = null;
  let facingMode = 'environment';
  let cameraModal = null;

  function stopCamera() {
    if (cameraStream) cameraStream.getTracks().forEach((t) => t.stop());
    cameraStream = null;
  }

  function closeCamera() {
    stopCamera();
    if (cameraModal) cameraModal.classList.add('hidden');
    document.documentElement.classList.remove('camera-open');
  }

  function buildCameraModal() {
    cameraModal = document.createElement('div');
    cameraModal.className = 'camera-overlay hidden';
    cameraModal.setAttribute('role', 'dialog');
    cameraModal.setAttribute('aria-modal', 'true');
    cameraModal.setAttribute('aria-label', 'Take a photo of the menu');
    cameraModal.innerHTML = `
      <div class="camera-box">
        <button type="button" class="camera-close" aria-label="Close camera">&times;</button>
        <div class="camera-view">
          <video class="camera-video" autoplay playsinline muted></video>
          <div class="camera-message" hidden></div>
        </div>
        <p class="camera-tip">Fit the dish names in the frame and hold steady.</p>
        <div class="camera-controls">
          <button type="button" class="camera-switch" aria-label="Switch camera">&#8635;</button>
          <button type="button" class="camera-shutter" aria-label="Take photo"><span></span></button>
          <span class="camera-spacer"></span>
        </div>
        <div class="camera-fallback" hidden>
          <button type="button" class="dropzone-btn dropzone-btn-camera camera-app-btn">Open Camera App</button>
          <button type="button" class="dropzone-btn camera-upload-btn">Upload Photo</button>
        </div>
      </div>`;
    document.body.appendChild(cameraModal);
    cameraModal.querySelector('.camera-close').addEventListener('click', closeCamera);
    cameraModal.addEventListener('click', (e) => { if (e.target === cameraModal) closeCamera(); });
    cameraModal.querySelector('.camera-switch').addEventListener('click', () => {
      facingMode = facingMode === 'environment' ? 'user' : 'environment';
      startCamera();
    });
    cameraModal.querySelector('.camera-shutter').addEventListener('click', takePicture);
    // Fallbacks run from a tap, so the browser allows opening the picker
    cameraModal.querySelector('.camera-app-btn').addEventListener('click', () => { closeCamera(); cameraInput.click(); });
    cameraModal.querySelector('.camera-upload-btn').addEventListener('click', () => { closeCamera(); fileInput.click(); });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && cameraModal && !cameraModal.classList.contains('hidden')) closeCamera();
    });
  }

  function showCameraProblem(text) {
    const msg = cameraModal.querySelector('.camera-message');
    msg.textContent = text;
    msg.hidden = false;
    cameraModal.querySelector('.camera-video').hidden = true;
    cameraModal.querySelector('.camera-controls').hidden = true;
    cameraModal.querySelector('.camera-tip').hidden = true;
    cameraModal.querySelector('.camera-fallback').hidden = false;
  }

  async function startCamera() {
    stopCamera();
    const video = cameraModal.querySelector('.camera-video');
    cameraModal.querySelector('.camera-message').hidden = true;
    cameraModal.querySelector('.camera-fallback').hidden = true;
    cameraModal.querySelector('.camera-controls').hidden = false;
    cameraModal.querySelector('.camera-tip').hidden = false;
    video.hidden = false;
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: facingMode }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      video.srcObject = cameraStream;
      await video.play().catch(() => {});
      // Only show the switch button when there is more than one camera
      const devices = await navigator.mediaDevices.enumerateDevices().catch(() => []);
      cameraModal.querySelector('.camera-switch').style.visibility =
        devices.filter((d) => d.kind === 'videoinput').length > 1 ? 'visible' : 'hidden';
    } catch (error) {
      const name = error && error.name;
      showCameraProblem(
        name === 'NotAllowedError'
          ? 'Camera access was blocked. Allow the camera for this site in your browser settings, or use the buttons below.'
          : name === 'NotFoundError'
            ? 'No camera was found on this device. You can upload a photo instead.'
            : 'This browser could not open the camera here. Try the buttons below.'
      );
    }
  }

  function takePicture() {
    const video = cameraModal.querySelector('.camera-video');
    if (!video.videoWidth) return;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      if (!blob) return;
      const file = new File([blob], `menu-photo-${Date.now()}.jpg`, { type: 'image/jpeg' });
      closeCamera();
      useCapturedFile(file);
    }, 'image/jpeg', 0.92);
  }

  function openCamera() {
    // No in-page camera (old browser, or the page is not on https): use the phone's camera app
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) || !window.isSecureContext) {
      cameraInput.click();
      return;
    }
    if (!cameraModal) buildCameraModal();
    cameraModal.classList.remove('hidden');
    document.documentElement.classList.add('camera-open');
    startCamera();
  }

  if (cameraBtn && cameraInput) {
    cameraBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      openCamera();
    });
    cameraInput.addEventListener('change', () => {
      const file = cameraInput.files[0];
      if (!file) return;
      cameraInput.value = '';
      useCapturedFile(file);
    });
  }
  if (uploadBtn) {
    uploadBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      fileInput.click();
    });
  }

  // Drag and drop
  ['dragenter', 'dragover'].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add('dragover');
  }));

  ['dragleave', 'drop'].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove('dragover');
  }));

  dropzone.addEventListener('drop', (event) => {
    const [file] = event.dataTransfer.files;
    if (file) {
      fileInput.files = event.dataTransfer.files;
      displayPreview(file);
    }
  });

  // Close picker
  pickerClose.addEventListener('click', closeDishPicker);

  picker.addEventListener('click', (event) => {
    if (event.target === picker) closeDishPicker();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !picker.classList.contains('hidden')) {
      closeDishPicker();
    }
  });

  // Reopen picker
  selectedDishCard.addEventListener('click', (event) => {
    event.stopPropagation();
    if (detectedMatches.length >= 2) {
      showDishPicker(detectedMatches);
    }
  });

  selectDishPrompt.addEventListener('click', (event) => {
    event.stopPropagation();
    if (detectedMatches.length >= 2) {
      showDishPicker(detectedMatches);
    }
  });

  // Remove photo and reset
  removeBtn.addEventListener('click', (event) => {
    event.stopPropagation();
    if (activeScanController) activeScanController.abort();
    setOcrLoading(false);
    fileInput.value = '';
    previewImg.src = '';
    fileName.textContent = '';
    hiddenDishInput.value = '';
    selectedDishCard.style.display = 'none';
    detectedMatches = [];
    closeDishPicker();
    setOrigin('');
    previewState.style.display = 'none';
    defaultState.style.display = 'block';
  });

  // Check before submit
  imageForm.addEventListener('submit', (event) => {
    // A dish already picked from the photo is enough (the camera photo may not be in fileInput on old phones)
    if (!fileInput.files[0] && !hiddenDishInput.value) {
      event.preventDefault();
      showErrorModal('Upload a Photo', 'Upload a photo of a menu first so we can find the dish and its country.');
      return;
    }
    if (!hiddenDishInput.value || !originSelect.value) {
      event.preventDefault();
      showErrorModal('Choose a Dish', 'Select one of the detected menu dishes before starting the comparison.');
    }
  });
});