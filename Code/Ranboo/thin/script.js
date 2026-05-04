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
	const form = document.getElementById("sign-in-form");
	const clock = document.getElementById("clock");
	const method = document.getElementById("method");

	const hostsByMethod = {
		eth: "10.42.1.1",
		usb: "10.12.194.1",
		wlo: "10.42.0.1"
	};

	const updateClock = () => {
		const now = new Date();
		clock.textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
	};

	const connectToVnc = () => {
		const host = hostsByMethod[method.value] || hostsByMethod.eth;
		const url = new URL("/vnc_lite.html", window.location.origin);
		url.searchParams.set("host", host);
		url.searchParams.set("port", "5900");
		window.location.assign(url.toString());
	};

	form.addEventListener("submit", (event) => {
		event.preventDefault();
		connectToVnc();
	});

	updateClock();
	setInterval(updateClock, 15000);
})();