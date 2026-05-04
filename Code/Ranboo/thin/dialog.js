export function createConnectionDialogs(statusElement) {
	let statusTimer = null;
	let errorDialogRaised = false;

	const setStatus = (text) => {
		if (!statusElement) {
			return;
		}

		statusElement.classList.remove("hidden");
		statusElement.textContent = text;

		if (statusTimer !== null) {
			window.clearTimeout(statusTimer);
			statusTimer = null;
		}

		if (text) {
			statusTimer = window.setTimeout(() => {
				statusElement.textContent = "";
				statusElement.classList.add("hidden");
				statusTimer = null;
			}, 7000);
		}
	};

	const clearErrorDialogState = () => {
		errorDialogRaised = false;
	};

	const showConnectionIssue = (message) => {
		if (errorDialogRaised) {
			return;
		}

		errorDialogRaised = true;
		window.setTimeout(() => {
			window.alert(message);
		}, 0);
	};

	return {
		setStatus,
		clearErrorDialogState,
		showConnectionIssue
	};
}