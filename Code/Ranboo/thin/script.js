import { baseLayerLuminance, StandardLuminance } from "./deps/fluent-web-components.min.js";

function applyColorMode() {
	const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
	baseLayerLuminance.setValueFor(
		document.body,
		prefersDark ? StandardLuminance.DarkMode : StandardLuminance.LightMode
	);
}

const colorModeQuery = window.matchMedia("(prefers-color-scheme: dark)");
colorModeQuery.addEventListener("change", applyColorMode);

applyColorMode();

(() => {
	const views = {
		signin: document.getElementById("view-signin"),
		connecting: document.getElementById("view-connecting"),
		error: document.getElementById("view-error")
	};

	const form = document.getElementById("sign-in-form");
	const retry = document.getElementById("retry");
	const signOut = document.getElementById("sign-out");
	const cancel = document.getElementById("cancel-connect");
	const timestamp = document.getElementById("timestamp");
	const correlation = document.getElementById("correlation");
	const clock = document.getElementById("clock");

	let connectTimer;

	const show = (key) => {
		Object.entries(views).forEach(([name, node]) => {
			node.classList.toggle("hidden", name !== key);
		});
	};

	const randomHex = (size) => {
		const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
		let out = "";
		for (let i = 0; i < size; i += 1) {
			out += chars[Math.floor(Math.random() * chars.length)];
		}
		return out;
	};

	const makeCorrelation = () => {
		return [8, 4, 4, 4, 12].map(randomHex).join("-");
	};

	const updateClock = () => {
		const now = new Date();
		clock.textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
	};

	const toError = () => {
		timestamp.textContent = new Date().toISOString();
		correlation.textContent = makeCorrelation();
		show("error");
	};

	const startConnectFlow = () => {
		clearTimeout(connectTimer);
		show("connecting");
		connectTimer = setTimeout(toError, 2300);
	};

	form.addEventListener("submit", (event) => {
		event.preventDefault();
		startConnectFlow();
	});

	retry.addEventListener("click", startConnectFlow);
	cancel.addEventListener("click", () => {
		clearTimeout(connectTimer);
		show("signin");
	});
	signOut.addEventListener("click", () => {
		clearTimeout(connectTimer);
		show("signin");
	});

	updateClock();
	setInterval(updateClock, 15000);
	show("signin");
})();