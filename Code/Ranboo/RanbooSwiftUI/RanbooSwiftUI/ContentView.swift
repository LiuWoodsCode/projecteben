//
//  ContentView.swift
//  RanbooSwiftUI
//
//  Created by Pixel Prowler on 8/25/26.
//

import SwiftUI
import Combine
import Network

#if os(iOS)
import UIKit
typealias PlatformImage = UIImage
#elseif os(macOS)
import AppKit
typealias PlatformImage = NSImage
#endif

// MARK: - Models

struct ServiceState {
    enum Status: Equatable {
        case notChecked
        case checking
        case online(String?)
        case offline(String)
        
        var label: String {
            switch self {
            case .notChecked:
                return "Not checked"
            case .checking:
                return "Checking..."
            case .online(let detail):
                return detail.map { "Online (\($0))" } ?? "Online"
            case .offline(let error):
                return "Offline: \(error)"
            }
        }
        
        var isOnline: Bool {
            if case .online = self { return true }
            return false
        }
    }
    
    var ssh: Status = .notChecked
    var http: Status = .notChecked
    var ranboo: Status = .notChecked
}

struct HostStatus: Identifiable {
    let id = UUID()
    let address: String
    var services = ServiceState()
}

struct DeviceInfo {
    var model = ""
    var hostname = ""
    var serial = ""
    var revision = ""
    var kernelVersion = ""
    var uptime = ""
}

struct MemoryInfo: Decodable {
    let ok: Bool
    let totalMiB: Double?
    let usedMiB: Double?
    let freeMiB: Double?
    let error: String?
    
    enum CodingKeys: String, CodingKey {
        case ok
        case totalMiB = "total_mib"
        case usedMiB = "used_mib"
        case freeMiB = "free_mib"
        case error
    }
}

struct ThermalInfo {
    var cpu: Double?
    var gpu: Double?
}

struct APIResponse: Decodable {
    let ok: Bool?
    let error: String?
    let wayvncPID: Int?
    let novncPID: Int?
    let stopped: Bool?
    let websocket: Bool?
    
    enum CodingKeys: String, CodingKey {
        case ok, error, stopped, websocket
        case wayvncPID = "wayvnc_pid"
        case novncPID = "novnc_pid"
    }
}

struct VNCStatusViewData {
    var description = "Not checked"
}

enum EbenAPIError: LocalizedError {
    case invalidURL
    case invalidResponse
    case httpStatus(Int, String)
    case invalidText(String)
    case api(String)
    
    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "The request URL could not be created."
        case .invalidResponse:
            return "The server returned an invalid HTTP response."
        case .httpStatus(let status, let body):
            return "HTTP \(status): \(body.isEmpty ? "No response body" : body)"
        case .invalidText(let path):
            return "The response from \(path) was not valid text."
        case .api(let message):
            return message
        }
    }
}

// MARK: - API Client

struct EbenAPIClient {
    let host: String
    let port: Int
    
    init(host: String, port: Int = 8000) {
        self.host = host
        self.port = port
    }
    
    private var baseURL: URL? {
        var components = URLComponents()
        components.scheme = "http"
        components.host = host
        components.port = port
        return components.url
    }
    
    private func request(
        _ path: String,
        method: String = "GET",
        body: Data? = nil,
        contentType: String? = nil,
        timeout: TimeInterval = 8
    ) async throws -> (Data, HTTPURLResponse) {
        guard let baseURL else { throw EbenAPIError.invalidURL }
        
        let cleanPath = path.hasPrefix("/") ? String(path.dropFirst()) : path
        let url = baseURL.appendingPathComponent(cleanPath)
        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = method
        request.httpBody = body
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        if let contentType {
            request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        }
        
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw EbenAPIError.invalidResponse
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            throw EbenAPIError.httpStatus(httpResponse.statusCode, message)
        }
        return (data, httpResponse)
    }
    
    func text(_ path: String) async throws -> String {
        let (data, _) = try await request(path)
        guard let value = String(data: data, encoding: .utf8) else {
            throw EbenAPIError.invalidText(path)
        }
        return value.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    
    func detectRanboo() async throws {
        let (data, response) = try await request("/test", timeout: 5)
        let body = String(decoding: data, as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let header = response.value(forHTTPHeaderField: "hello")
        
        guard body == "Hello", header == "content" else {
            throw EbenAPIError.api(
                "Port 8000 answered, but /test did not match the Ranboo/Eben API signature."
            )
        }
    }
    
    func deviceInfo() async throws -> DeviceInfo {
        async let model = text("/device/model")
        async let hostname = text("/device/hostname")
        async let serial = text("/device/serial")
        async let revision = text("/device/revision")
        async let kernelVersion = text("/kernel/version")
        async let uptime = text("/session/uptime")
        
        return try await DeviceInfo(
            model: model,
            hostname: hostname,
            serial: serial,
            revision: revision,
            kernelVersion: kernelVersion,
            uptime: uptime
        )
    }
    
    func memory() async throws -> MemoryInfo {
        let (data, _) = try await request("/device/resource/mem")
        let result = try JSONDecoder().decode(MemoryInfo.self, from: data)
        guard result.ok else {
            throw EbenAPIError.api(result.error ?? "Unable to read memory usage.")
        }
        return result
    }
    
    func thermal() async throws -> ThermalInfo {
        async let cpuText = text("/thermal/cpu")
        async let gpuText = text("/thermal/gpu")
        let (cpuValue, gpuValue) = try await (cpuText, gpuText)
        
        guard let cpu = Double(cpuValue), let gpu = Double(gpuValue) else {
            throw EbenAPIError.api("The server returned an invalid temperature value.")
        }
        return ThermalInfo(cpu: cpu, gpu: gpu)
    }
    
    func command(_ path: String, method: String = "POST") async throws -> APIResponse {
        let (data, _) = try await request(path, method: method)
        let result = try JSONDecoder().decode(APIResponse.self, from: data)
        if result.ok == false {
            throw EbenAPIError.api(result.error ?? "The API command failed.")
        }
        return result
    }
    
    func vncStatus() async throws -> APIResponse {
        let (data, _) = try await request("/vnc/status")
        return try JSONDecoder().decode(APIResponse.self, from: data)
    }
    
    func preview() async throws -> PlatformImage {
        let (data, _) = try await request("/vnc/preview", timeout: 15)
        guard let image = PlatformImage(data: data) else {
            throw EbenAPIError.api("The VNC preview response was not a valid image.")
        }
        return image
    }
}

// MARK: - Discovery

@MainActor
final class HostChecker: ObservableObject {
    @Published var hosts: [HostStatus] = [
        HostStatus(address: "10.42.0.1"),
        HostStatus(address: "10.42.1.1"),
        HostStatus(address: "10.12.194.1")
    ]
    
    private var connections: [UUID: NWConnection] = [:]
    
    func checkAll() {
        for host in hosts {
            checkHost(id: host.id)
        }
    }
    
    func checkHost(id: UUID) {
        guard let index = hosts.firstIndex(where: { $0.id == id }) else { return }
        let address = hosts[index].address
        hosts[index].services = ServiceState(ssh: .checking, http: .checking, ranboo: .checking)
        
        checkTCP(hostID: id, address: address, port: 22, service: .ssh)
        checkTCP(hostID: id, address: address, port: 8000, service: .http)
        
        Task {
            do {
                try await EbenAPIClient(host: address).detectRanboo()
                update(id: id, service: .ranboo, status: .online("Eben Desktop API"))
            } catch {
                update(id: id, service: .ranboo, status: .offline(Self.describe(error)))
            }
        }
    }
    
    private enum CheckedService { case ssh, http, ranboo }
    
    private func checkTCP(hostID: UUID, address: String, port: UInt16, service: CheckedService) {
        guard let nwPort = NWEndpoint.Port(rawValue: port) else { return }
        let connection = NWConnection(host: NWEndpoint.Host(address), port: nwPort, using: .tcp)
        connections[hostID] = connection
        
        connection.stateUpdateHandler = { [weak self, weak connection] state in
            Task { @MainActor in
                guard let self else { return }
                switch state {
                case .ready:
                    self.update(id: hostID, service: service, status: .online("TCP \(port)"))
                    connection?.cancel()
                case .failed(let error):
                    self.update(id: hostID, service: service, status: .offline(error.localizedDescription))
                    connection?.cancel()
                case .waiting(let error):
                    self.update(id: hostID, service: service, status: .offline(error.localizedDescription))
                default:
                    break
                }
            }
        }
        connection.start(queue: DispatchQueue.global(qos: .userInitiated))
    }
    
    private func update(id: UUID, service: CheckedService, status: ServiceState.Status) {
        guard let index = hosts.firstIndex(where: { $0.id == id }) else { return }
        switch service {
        case .ssh: hosts[index].services.ssh = status
        case .http: hosts[index].services.http = status
        case .ranboo: hosts[index].services.ranboo = status
        }
    }
    
    static func describe(_ error: Error) -> String {
        let nsError = error as NSError
        return "\(error.localizedDescription) [\(nsError.domain) \(nsError.code)]"
    }
}

// MARK: - Device Detail

@MainActor
final class DeviceViewModel: ObservableObject {
    @Published var info = DeviceInfo()
    @Published var memory: MemoryInfo?
    @Published var thermal = ThermalInfo()
    @Published var vnc = VNCStatusViewData()
    @Published var previewImage: PlatformImage?
    @Published var isLoading = false
    @Published var statusMessage: String?
    @Published var errorMessage: String?
    
    let host: String
    private var api: EbenAPIClient { EbenAPIClient(host: host) }
    
    init(host: String) {
        self.host = host
    }
    
    func refresh() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        
        do {
            async let infoRequest = api.deviceInfo()
            async let memoryRequest = api.memory()
            async let thermalRequest = api.thermal()
            async let vncRequest = api.vncStatus()
            
            let (newInfo, newMemory, newThermal, newVNC) = try await (
                infoRequest, memoryRequest, thermalRequest, vncRequest
            )
            info = newInfo
            memory = newMemory
            thermal = newThermal
            applyVNCStatus(newVNC)
        } catch {
            errorMessage = HostChecker.describe(error)
        }
    }
    
    func runVNC(_ path: String) async {
        await perform("VNC command completed") {
            let response = try await api.command(path)
            applyVNCStatus(response)
        }
    }
    
    func loadPreview() async {
        await perform("Preview updated") {
            previewImage = try await api.preview()
        }
    }
    
    func power(_ path: String) async {
        await perform("Power command sent") {
            _ = try await api.command(path, method: "GET")
        }
    }

    func runIronmouse(_ action: IronmouseAction) async {
        await perform("\(action.title) completed") {
            _ = try await api.command(action.path, method: "GET")
        }
    }
    
    private func perform(_ success: String, action: () async throws -> Void) async {
        statusMessage = nil
        errorMessage = nil
        do {
            try await action()
            statusMessage = success
        } catch {
            errorMessage = HostChecker.describe(error)
        }
    }
    
    private func applyVNCStatus(_ response: APIResponse) {
        var parts: [String] = []
        if let pid = response.wayvncPID { parts.append("wayvnc PID \(pid)") }
        if let pid = response.novncPID { parts.append("noVNC PID \(pid)") }
        if response.websocket == true { parts.append("WebSocket enabled") }
        if response.stopped == true { parts.append("Stopped") }
        vnc.description = parts.isEmpty ? "No tracked VNC process" : parts.joined(separator: ", ")
    }
}

// MARK: - Views

struct ContentView: View {
    @StateObject private var checker = HostChecker()
    
    var body: some View {
        NavigationStack {
            List {
                ForEach(checker.hosts) { host in
                    NavigationLink {
                        DeviceDetailView(host: host.address)
                    } label: {
                        HostRow(host: host)
                    }
                    .disabled(!host.services.ranboo.isOnline)
                    .swipeActions {
                        Button("Check") { checker.checkHost(id: host.id) }
                            .tint(.blue)
                    }
                }
            }
            .navigationTitle("Eben Desktop")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        checker.checkAll()
                    } label: {
                        Label("Refresh", systemImage: "arrow.clockwise")
                    }
                }
            }
            .task {
                checker.checkAll()
            }
        }
    }
}

struct HostRow: View {
    let host: HostStatus
    
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(host.address)
                .font(.headline.monospaced())
            ServiceLine(name: "SSH", status: host.services.ssh)
            ServiceLine(name: "HTTP", status: host.services.http)
            ServiceLine(name: "Ranboo", status: host.services.ranboo)
        }
        .padding(.vertical, 4)
    }
}

struct ServiceLine: View {
    let name: String
    let status: ServiceState.Status
    
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Circle()
                .fill(status.isOnline ? Color.green : status == .checking ? Color.orange : Color.red)
                .frame(width: 8, height: 8)
            Text(name)
                .font(.subheadline.weight(.semibold))
                .frame(width: 58, alignment: .leading)
            Text(status.label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(3)
        }
    }
}

struct DeviceDetailView: View {
    @StateObject private var model: DeviceViewModel
    @State private var pendingPowerAction: PowerAction?
    
    init(host: String) {
        _model = StateObject(wrappedValue: DeviceViewModel(host: host))
    }
    
    var body: some View {
        List {
            if model.isLoading {
                Section { ProgressView("Loading device data...") }
            }
            
            Section("Device") {
                LabeledContent("Hostname", value: display(model.info.hostname))
                LabeledContent("Model", value: display(model.info.model))
                LabeledContent("Serial", value: display(model.info.serial))
                LabeledContent("Revision", value: display(model.info.revision))
                LabeledContent("Uptime", value: formattedUptime(model.info.uptime))
                LabeledContent("Kernel", value: display(model.info.kernelVersion))
            }
            
            Section("Memory") {
                if let memory = model.memory,
                   let used = memory.usedMiB,
                   let total = memory.totalMiB,
                   total > 0 {
                    LabeledContent("Used", value: "\(format(used)) / \(format(total)) MiB")
                    ProgressView(value: used, total: total)
                    if let free = memory.freeMiB {
                        LabeledContent("Free", value: "\(format(free)) MiB")
                    }
                } else {
                    Text("No memory data")
                        .foregroundStyle(.secondary)
                }
            }
            
            Section("Thermals") {
                temperatureRow("CPU", value: model.thermal.cpu)
                temperatureRow("GPU", value: model.thermal.gpu)
            }
            
            Section("Remote Desktop") {
                Text(model.vnc.description)
                    .foregroundStyle(.secondary)
                Button("Start VNC") { Task { await model.runVNC("/vnc/start") } }
                Button("Start VNC with WebSocket") { Task { await model.runVNC("/vnc/start/websocket") } }
                Button("Start noVNC") { Task { await model.runVNC("/vnc/start/novnc") } }
                Button("Stop VNC", role: .destructive) { Task { await model.runVNC("/vnc/stop") } }
                Button("Stop noVNC", role: .destructive) { Task { await model.runVNC("/vnc/stop/novnc") } }
                Button("Load Preview") { Task { await model.loadPreview() } }
                
                if let image = model.previewImage {
                    platformImage(image)
                        .resizable()
                        .scaledToFit()
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            }

            Section("Ironmouse") {
                ForEach(IronmouseAction.allCases) { action in
                    Button(action.title, role: action.role) {
                        Task { await model.runIronmouse(action) }
                    }
                }
            }
            
            Section("Power") {
                ForEach(PowerAction.allCases) { action in
                    Button(action.title, role: action.role) {
                        pendingPowerAction = action
                    }
                }
            }
            
            if let message = model.statusMessage {
                Section { Text(message).foregroundStyle(.green) }
            }
            if let error = model.errorMessage {
                Section("Networking Error") {
                    Text(error)
                        .font(.caption.monospaced())
                        .textSelection(.enabled)
                }
            }
        }
        .navigationTitle(model.info.hostname.isEmpty ? model.host : model.info.hostname)
        .toolbar {
            Button {
                Task { await model.refresh() }
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
        }
        .task { await model.refresh() }
        .confirmationDialog(
            pendingPowerAction.map { "\($0.title) \(model.host)?" } ?? "Power action",
            isPresented: Binding(
                get: { pendingPowerAction != nil },
                set: { if !$0 { pendingPowerAction = nil } }
            ),
            titleVisibility: .visible
        ) {
            if let action = pendingPowerAction {
                Button(action.title, role: action.role) {
                    pendingPowerAction = nil
                    Task { await model.power(action.path) }
                }
            }
            Button("Cancel", role: .cancel) { pendingPowerAction = nil }
        } message: {
            Text("This command affects the remote device immediately.")
        }
    }
    
    private func display(_ value: String) -> String {
        value.isEmpty ? "Unknown" : value
    }
    
    private func format(_ value: Double) -> String {
        value.formatted(.number.precision(.fractionLength(0...1)))
    }
    
    private func formattedUptime(_ raw: String) -> String {
        guard let secondsText = raw.split(separator: " ").first,
              let seconds = Double(secondsText) else { return display(raw) }
        let duration = Duration.seconds(seconds)
        return duration.formatted(.units(allowed: [.days, .hours, .minutes], width: .abbreviated))
    }
    
    @ViewBuilder
    private func temperatureRow(_ label: String, value: Double?) -> some View {
        if let value {
            LabeledContent(label, value: "\(format(value)) °C")
            Gauge(value: value, in: 0...100) { Text(label) }
                .tint(value >= 80 ? .red : value >= 65 ? .orange : .green)
        } else {
            LabeledContent(label, value: "Unknown")
        }
    }
    
    private func platformImage(_ image: PlatformImage) -> Image {
#if os(iOS)
        return Image(uiImage: image)
#elseif os(macOS)
        return Image(nsImage: image)
#endif
    }
}

enum IronmouseAction: String, CaseIterable, Identifiable {
    case enableAccessPoint
    case disableAccessPoint
    case enableEthernetSharing
    case disableEthernetSharing

    var id: String { rawValue }

    var path: String {
        switch self {
        case .enableAccessPoint: return "/ironmouse/ap/enable"
        case .disableAccessPoint: return "/ironmouse/ap/disable"
        case .enableEthernetSharing: return "/ironmouse/eth/enable"
        case .disableEthernetSharing: return "/ironmouse/eth/disable"
        }
    }

    var title: String {
        switch self {
        case .enableAccessPoint: return "Enable Access Point"
        case .disableAccessPoint: return "Disable Access Point"
        case .enableEthernetSharing: return "Enable Ethernet Sharing"
        case .disableEthernetSharing: return "Disable Ethernet Sharing"
        }
    }

    var role: ButtonRole? {
        switch self {
        case .disableAccessPoint, .disableEthernetSharing: return .destructive
        case .enableAccessPoint, .enableEthernetSharing: return nil
        }
    }
}

enum PowerAction: String, CaseIterable, Identifiable {
    case restart, sleep, hibernate, poweroff
    
    var id: String { rawValue }
    var path: String { "/power/\(rawValue)" }
    
    var title: String {
        switch self {
        case .restart: return "Restart"
        case .sleep: return "Sleep"
        case .hibernate: return "Hibernate"
        case .poweroff: return "Power Off"
        }
    }
    
    var role: ButtonRole? {
        switch self {
        case .restart, .poweroff: return .destructive
        case .sleep, .hibernate: return nil
        }
    }
}

#Preview {
    ContentView()
}
