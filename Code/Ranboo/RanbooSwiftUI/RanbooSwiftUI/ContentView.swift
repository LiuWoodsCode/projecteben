import SwiftUI
import Network
import Combine
import PhotosUI
import Photos
import UniformTypeIdentifiers

#if os(iOS)
import UIKit
typealias PlatformImage = UIImage
#elseif os(macOS)
import AppKit
typealias PlatformImage = NSImage
#endif

// MARK: - Models
struct ClientInfo {
    static let installID: String = {
        if let existing = UserDefaults.standard.string(forKey: "InstallID") {
            return existing
        }
        
        let id = UUID().uuidString
        UserDefaults.standard.set(id, forKey: "InstallID")
        return id
    }()
    
    static func headers() -> [String: String] {
        var headers: [String: String] = [:]
        
        let version =
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "unknown"
        
#if os(iOS)
        let device = UIDevice.current
        
        headers["X-Client-Platform"] = "iOS"
        headers["X-Client-Model"] = device.model
        headers["X-Client-Name"] = device.name
        headers["X-Client-OS"] = device.systemVersion
        
#elseif os(macOS)
        let processInfo = ProcessInfo.processInfo
        
        headers["X-Client-Platform"] = "macOS"
        headers["X-Client-Name"] = Host.current().localizedName ?? "Unknown"
        headers["X-Client-OS"] =
        processInfo.operatingSystemVersionString
#endif
        
        headers["X-Client-App"] = "Ranboo SwiftUI"
        headers["X-Client-Version"] = version
        headers["X-Client-ID"] = installID
        
        return headers
    }
}

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
                return "Error: \(error)"
            }
        }
        
        var isOnline: Bool {
            if case .online = self { return true }
            return false
        }
        
        var indicatorColor: Color {
            switch self {
            case .checking:
                return .orange
            case .online:
                return .green
            case .notChecked, .offline:
                return .red
            }
        }
    }
    
    var ssh: Status = .notChecked
    var http: Status = .notChecked
    var ios: Status = .notChecked
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

struct DiskInfo: Decodable, Identifiable {
    let device: String
    let mountpoint: String
    let fstype: String
    let opts: String
    let usage: DiskUsage

    var id: String { "\(device)|\(mountpoint)" }
}

struct DiskUsage: Decodable {
    let total: Double
    let used: Double
    let free: Double
    let percent: Double
}

struct ThermalInfo {
    var cpu: Double?
    var gpu: Double?
    var pmic: Double?
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
        print("eben API request to fetch \(url) with method \(method) and timeout \(timeout)")
        if String(method) == "GET" {
            print("follow up not needed for \(method), as it is get and has no body")
        } else if let body {
            print("follow up to \(url): \(String(data: body, encoding: .utf8) ?? "<non-UTF8 body>")")
        } else {
            print("follow up to \(url): <no body>")
        }
        
        var request = URLRequest(url: url, timeoutInterval: timeout)
        
        for (key, value) in ClientInfo.headers() {
            request.setValue(value, forHTTPHeaderField: key)
        }
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
                "Port 8000 answered, but /test did not match the Ranboo API signature."
            )
        }
    }
    
    func detectIfAvailableWithOS() async throws {
        print("going to see if OS Http stack will talk to ranboo on \(host)...")
        _ = try await request("/test", timeout: 5)
        print("That worked for \(host)!")
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

    func disks() async throws -> [DiskInfo] {
        let (data, _) = try await request("/device/resource/disk")
        return try JSONDecoder().decode([DiskInfo].self, from: data)
    }
    
    func thermal() async throws -> ThermalInfo {
        async let cpuText = text("/thermal/cpu")
        async let gpuText = text("/thermal/gpu")
        async let pmicText = text("/thermal/pmic")
        let (cpuValue, gpuValue, pmicValue) = try await (cpuText, gpuText, pmicText)
        
        // hope that it gives us a temp we can use as a double
        guard let cpu = Double(cpuValue), let gpu = Double(gpuValue), let pmic = Double(pmicValue) else {
            throw EbenAPIError.api("The server returned an invalid temperature value.")
        }
        return ThermalInfo(cpu: cpu, gpu: gpu, pmic: pmic)
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
        print("get vncStatus triggered")
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
    
    func upload(_ data: Data, originalFilename: String) async throws {
        guard let baseURL else { throw EbenAPIError.invalidURL }
        
        let filename = (originalFilename as NSString).lastPathComponent
        guard !filename.isEmpty, filename != ".", filename != ".." else {
            throw EbenAPIError.api("The selected file does not have a valid filename.")
        }
        
        var components = URLComponents(
            url: baseURL.appendingPathComponent("files/upload"),
            resolvingAgainstBaseURL: false
        )
        components?.queryItems = [
            URLQueryItem(name: "path", value: "Downloads/\(filename)")
        ]
        print("File will be saved on host to Downloads/\(filename)")
        guard let url = components?.url else { throw EbenAPIError.invalidURL }
        
        var uploadRequest = URLRequest(url: url, timeoutInterval: 300)
        uploadRequest.httpMethod = "POST"
        uploadRequest.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        uploadRequest.setValue(
            UTType(filenameExtension: (filename as NSString).pathExtension)?.preferredMIMEType
            ?? "application/octet-stream",
            forHTTPHeaderField: "Content-Type"
        )
        
        let (responseData, response) = try await URLSession.shared.upload(
            for: uploadRequest,
            from: data
        )
        guard let httpResponse = response as? HTTPURLResponse else {
            throw EbenAPIError.invalidResponse
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: responseData, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            throw EbenAPIError.httpStatus(httpResponse.statusCode, message)
        }
    }
}

// MARK: - Discovery

@MainActor
final class HostChecker: ObservableObject {
    @Published var hosts: [HostStatus] = [
        HostStatus(address: "10.42.0.1"), // wifi ap
        HostStatus(address: "10.42.1.1"), // eth share
        HostStatus(address: "10.12.194.1") // usb gadget
    ]
    
    private var connections: [UUID: [CheckedService: NWConnection]] = [:]
    
    func checkAll() {
        for host in hosts {
            checkHost(id: host.id)
        }
    }
    
    func checkHost(id: UUID) {
        guard let index = hosts.firstIndex(where: { $0.id == id }) else { return }
        let address = hosts[index].address
        hosts[index].services = ServiceState(
            ssh: .checking,
            http: .checking,
            ios: .checking,
            ranboo: .checking
        )
        
        checkTCP(hostID: id, address: address, port: 22, service: .ssh)
        checkTCP(hostID: id, address: address, port: 8000, service: .http)
        
        Task {
            do {
                try await EbenAPIClient(host: address).detectIfAvailableWithOS()
                update(id: id, service: .ios, status: .online("HTTP service exists"))
            } catch {
                update(id: id, service: .ios, status: .offline(Self.describe(error)))
            }
            
            do {
                try await EbenAPIClient(host: address).detectRanboo()
                update(id: id, service: .ranboo, status: .online("Eben Desktop API"))
            } catch {
                update(id: id, service: .ranboo, status: .offline(Self.describe(error)))
            }
        }
    }
    
    private enum CheckedService: Hashable { case ssh, http, ios, ranboo }
    
    private func checkTCP(hostID: UUID, address: String, port: UInt16, service: CheckedService) {
        guard let nwPort = NWEndpoint.Port(rawValue: port) else { return }
        let connection = NWConnection(host: NWEndpoint.Host(address), port: nwPort, using: .tcp)
        connections[hostID, default: [:]][service] = connection
        
        connection.stateUpdateHandler = { [weak self, weak connection] state in
            Task { @MainActor in
                guard let self else { return }
                switch state {
                case .ready:
                    self.update(id: hostID, service: service, status: .online("TCP \(port)"))
                    connection?.cancel()
                    self.connections[hostID]?[service] = nil
                case .failed(let error):
                    self.update(id: hostID, service: service, status: .offline(error.localizedDescription))
                    connection?.cancel()
                    self.connections[hostID]?[service] = nil
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
        case .ios: hosts[index].services.ios = status
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
    @Published var disks: [DiskInfo] = []
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
            async let disksRequest = api.disks()
            async let thermalRequest = api.thermal()
            async let vncRequest = api.vncStatus()
            
            let (newInfo, newMemory, newDisks, newThermal, newVNC) = try await (
                infoRequest, memoryRequest, disksRequest, thermalRequest, vncRequest
            )
            info = newInfo
            memory = newMemory
            disks = newDisks
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
    
    func sendFile(data: Data, originalFilename: String) async {
        await perform("Sent \(originalFilename) to ~/Downloads/\(originalFilename)") {
            try await api.upload(data, originalFilename: originalFilename)
        }
    }
    
    func sendFile(at url: URL) async {
        let hasSecurityScope = url.startAccessingSecurityScopedResource()
        defer {
            if hasSecurityScope { url.stopAccessingSecurityScopedResource() }
        }
        
        await perform("Sent \(url.lastPathComponent) to ~/Downloads/\(url.lastPathComponent)") {
            let resourceValues = try url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
            guard resourceValues.isRegularFile == true else {
                // this is either a directory or symblink
                throw EbenAPIError.api("The selected item is not a regular file.")
            }
            if let size = resourceValues.fileSize, size > 512 * 1024 * 1024 {
                throw EbenAPIError.api("The selected file is larger than the 512 MB upload limit.")
            }
            let data = try Data(contentsOf: url, options: .mappedIfSafe)
            try await api.upload(data, originalFilename: url.lastPathComponent)
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
    @State private var selectedHostID: HostStatus.ID?
    @State private var columnVisibility: NavigationSplitViewVisibility = .all
    
    private var selectedHost: HostStatus? {
        checker.hosts.first { $0.id == selectedHostID }
    }
    
    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            List(selection: $selectedHostID) {
                Section {
                    Text("This app is not finalized and is meant for developers and platform engineers. This app contains options that when used improperly, may cause destruction of data. For most uses, users should continue to use the Ranboo web UI.")
                        .font(.callout)
                        .lineLimit(nil)
                        .fixedSize(horizontal: false, vertical: true)
                        .foregroundStyle(.secondary)
                    
                    ForEach(checker.hosts) { host in
                        NavigationLink(value: host.id) {
                            HostRow(host: host)
                        }
                        .disabled(!host.services.ranboo.isOnline)
                        .swipeActions {
                            Button("Check") {
                                checker.checkHost(id: host.id)
                            }
                            .tint(.blue)
                        }
                        .contextMenu {
                            Button {
                                checker.checkHost(id: host.id)
                            } label: {
                                Label("Check Device", systemImage: "arrow.clockwise")
                            }
                        }
                    }
                }
            }
            .navigationTitle("Ranboo SwiftUI")
            .navigationSplitViewColumnWidth(min: 320, ideal: 390, max: 480)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        checker.checkAll()
                    } label: {
                        Label("Refresh All", systemImage: "arrow.clockwise")
                    }
                }
            }
        } detail: {
            if let selectedHost {
                DeviceDetailView(host: selectedHost.address)
                    .id(selectedHost.id)
            } else {
                ContentUnavailableView(
                    "Select a Device",
                    systemImage: "desktopcomputer",
                    description: Text("Choose an available Ranboo device from the sidebar.")
                )
            }
        }
        .navigationSplitViewStyle(.balanced)
        .task {
            checker.checkAll()
        }
        .onReceive(checker.$hosts) { hosts in
            // guard selectedHostID == nil else { return }
            // selectedHostID = hosts.first(where: { $0.services.ranboo.isOnline })?.id
        }
    }
}

struct HostRow: View {
    let host: HostStatus
    
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(host.address)
                .font(.headline.monospaced())
            // Lightspeed Filter on iPadOS (most likely, but could be SentinelOne too) actually breaks local networking if you don't have some sort of WAN connection.
            // basically, the TCP sockets still can be reached but it makes the iOS networking stack never send any data, thus causing timeouts and other weird issues
            // If you try to go to the web UI in this state, it may appear that Ranboo might have crashed, as it will just say it's loading forever
            // This app was actually initally just this, simply letting me see where in the chain were we failing at, but then realized that using the same Qt client across Windows, macOS, and Linux was bad
            // as the Qt client is weird, it's ugly, and don't work on iPhones, iPads, and that $3500 headset
            // ahem, back to networking
            // This even causes weirdness in the Files app when syncing files to iCloud!
            // this might be moved into a debug page you can access with an option on the device page but for now this is good enough to diagnose connection issues
            // (who do I report this to? Apple? Lightspeed? SentinelOne? All?)
            ServiceLine(name: "SSH", status: host.services.ssh) // misc, is the system a typical Linux system at all?
            ServiceLine(name: "Ranboo (TCP)", status: host.services.http) // can we talk to the HTTP service on port 8000 using raw TCP?
            ServiceLine(name: "Ranboo (iOS)", status: host.services.ios) // can iOS's networking stack reach port 8000 at all?
            ServiceLine(name: "Ranboo", status: host.services.ranboo) // same as the last one but also checks specifically for ranboo, not
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }
}

struct ServiceLine: View {
    let name: String
    let status: ServiceState.Status
    
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Circle()
                .fill(status.indicatorColor)
                .frame(width: 8, height: 8)
            Text(name)
                .font(.subheadline.weight(.semibold))
                .frame(width: 113, alignment: .leading)
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
    @State private var pendingIronmouseApAction: IronmouseApAction?
    @State private var pendingIronmouseEthAction: IronmouseEthAction?
    @State private var isShowingFileImporter = false
    @State private var selectedPhotoItem: PhotosPickerItem?
    @Environment(\.openURL) private var openURL
    
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
                Button("Go to Ranboo WebUI") {
                    if let url = URL(string: "http://\(model.host):8000") {
                        openURL(url)
                    }
                }
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

            Section("Disks") {
                if model.disks.isEmpty {
                    Text("No disk data")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(model.disks) { disk in
                        VStack(alignment: .leading, spacing: 8) {
                            LabeledContent(disk.mountpoint, value: disk.device)
                                .font(.headline)
                            LabeledContent(
                                "Used",
                                value: "\(formatBytes(disk.usage.used)) / \(formatBytes(disk.usage.total))"
                            )
                            ProgressView(value: disk.usage.used, total: max(disk.usage.total, 1))
                            LabeledContent("Free", value: formatBytes(disk.usage.free))
                            LabeledContent("Filesystem", value: display(disk.fstype))
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
            
            Section("Thermals") {
                temperatureRow("CPU", value: model.thermal.cpu)
                temperatureRow("GPU", value: model.thermal.gpu)
                temperatureRow("PMIC", value: model.thermal.pmic)
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
                Button("Launch noVNC") {
                    if let url = URL(string: "http://\(model.host):6080/vnc.html") {
                        openURL(url)
                    }
                }
                
                if let image = model.previewImage {
                    platformImage(image)
                        .resizable()
                        .scaledToFit()
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            }

            // For now I can't think of a good way to implement Ranboo -> Client file transfer so for now, only implement Client -> Ranboo
            Section("Send File") {
                PhotosPicker(selection: $selectedPhotoItem, matching: .any(of: [.images, .videos])) {
                    Label("Choose from Photos Library", systemImage: "photo.on.rectangle")
                }
                
                Button {
                    isShowingFileImporter = true
                } label: {
                    Label("Choose from Files", systemImage: "folder")
                }
                
                Text("The selected file will be uploaded to ~/Downloads using its original filename.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            
            Section("Ironmouse AP") {
                ForEach(IronmouseApAction.allCases) { action in
                    Button(action.title, role: action.role) {
                        Task { await model.power(action.path) }
                    }
                }
            }

            Section("Ironmouse Eth") {
                ForEach(IronmouseEthAction.allCases) { action in
                    Button(action.title, role: action.role) {
                        Task { await model.power(action.path) }
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
            
            Section("Debug") {
                Text("IP: \(model.host)\nisLoading: \(model.isLoading)")
                    .monospaced()
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
#if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
#endif
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    Task { await model.refresh() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
        }
        .task { await model.refresh() }
        .fileImporter(
            isPresented: $isShowingFileImporter,
            allowedContentTypes: [.item],
            allowsMultipleSelection: false
        ) { result in
            switch result {
            case .success(let urls):
                guard let url = urls.first else { return }
                Task { await model.sendFile(at: url) }
            case .failure(let error):
                model.errorMessage = HostChecker.describe(error)
            }
        }
        .onChange(of: selectedPhotoItem) { item in
            guard let item else { return }
            Task { await sendPhoto(item) }
        }
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
        
        .confirmationDialog(
            pendingIronmouseApAction.map { "\($0.title) Ironmouse AP?" } ?? "Ironmouse AP action",
            isPresented: Binding(
                get: { pendingIronmouseApAction != nil },
                set: { if !$0 { pendingIronmouseApAction = nil } }
            ),
            titleVisibility: .visible
        ) {
            if let action = pendingIronmouseApAction {
                Button(action.title, role: action.role) {
                    pendingIronmouseApAction = nil
                    Task { await model.power(action.path) }
                }
            }

            Button("Cancel", role: .cancel) {
                pendingIronmouseApAction = nil
            }
        } message: {
            Text("This command affects the remote device immediately.")
        }

        .confirmationDialog(
            pendingIronmouseEthAction.map { "\($0.title) Ironmouse Ethernet?" } ?? "Ironmouse Ethernet action",
            isPresented: Binding(
                get: { pendingIronmouseEthAction != nil },
                set: { if !$0 { pendingIronmouseEthAction = nil } }
            ),
            titleVisibility: .visible
        ) {
            if let action = pendingIronmouseEthAction {
                Button(action.title, role: action.role) {
                    pendingIronmouseEthAction = nil
                    Task { await model.power(action.path) }
                }
            }

            Button("Cancel", role: .cancel) {
                pendingIronmouseEthAction = nil
            }
        } message: {
            Text("This command affects the remote device immediately.")
        }
    }
    
    private func sendPhoto(_ item: PhotosPickerItem) async {
        do {
            guard let data = try await item.loadTransferable(type: Data.self) else {
                throw EbenAPIError.api("The selected Photos item could not be loaded.")
            }
            // If you record in 4K60 on your iPhone this *may* be a problem
            guard data.count <= 512 * 1024 * 1024 else {
                throw EbenAPIError.api("The selected file is larger than the 512 MB upload limit.")
            }
            
            let filename = originalPhotoFilename(for: item)
            ?? fallbackPhotoFilename(for: item)
            await model.sendFile(data: data, originalFilename: filename)
        } catch {
            model.errorMessage = HostChecker.describe(error)
        }
        selectedPhotoItem = nil
    }
    
    private func originalPhotoFilename(for item: PhotosPickerItem) -> String? {
        guard let identifier = item.itemIdentifier else { return nil }
        let result = PHAsset.fetchAssets(withLocalIdentifiers: [identifier], options: nil)
        guard let asset = result.firstObject else { return nil }
        let finalName = PHAssetResource.assetResources(for: asset).first?.originalFilename
        print("Saving as \(finalName)")
        return finalName
    }
    
    private func fallbackPhotoFilename(for item: PhotosPickerItem) -> String {
        let type = item.supportedContentTypes.first
        let extensionPart = type?.preferredFilenameExtension.map { ".\($0)" } ?? ""
        let finalName = "Photo-\(UUID().uuidString)\(extensionPart)"
        print("Whoops, couldn't get original file name from Photos, so will save picture as \(finalName)")
        return finalName
    }
    
    private func display(_ value: String) -> String {
        value.isEmpty ? "Unknown" : value
    }
    
    private func format(_ value: Double) -> String {
        value.formatted(.number.precision(.fractionLength(0...1)))
    }

    private func formatBytes(_ value: Double) -> String {
        ByteCountFormatter.string(
            fromByteCount: Int64(value.rounded()),
            countStyle: .file
        )
    }
    
    private func formattedUptime(_ raw: String) -> String {
        guard let secondsText = raw.split(separator: " ").first,
              let seconds = Double(secondsText) else { return display(raw) }
        let duration = Duration.seconds(seconds)
        return duration.formatted(
            .units(allowed: [.days, .hours, .minutes], width: .abbreviated)
        )
    }
    
    @ViewBuilder
    private func temperatureRow(_ label: String, value: Double?) -> some View {
        if let value {
            // todo: we should probably convert this to farenhight if you have your temp unit in locale set to that
            LabeledContent(label, value: "\(format(value)) °C")
            Gauge(value: value, in: 0...100) { EmptyView() }
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

enum IronmouseApAction: String, CaseIterable, Identifiable {
    case enable, disable
    
    var id: String { rawValue }
    var path: String { "/ironmouse/ap/\(rawValue)" }
    
    var title: String {
        switch self {
        case .enable: return "Enable"
        case .disable: return "Disable"
        }
    }
    
    var role: ButtonRole? {
        switch self {
        case .enable, .disable: return nil
        }
    }
}


enum IronmouseEthAction: String, CaseIterable, Identifiable {
    case enable, disable
    
    var id: String { rawValue }
    var path: String { "/ironmouse/eth/\(rawValue)" }
    
    var title: String {
        switch self {
        case .enable: return "Enable"
        case .disable: return "Disable"
        }
    }
    
    var role: ButtonRole? {
        switch self {
        case .enable, .disable: return nil
        }
    }
}

#Preview {
    ContentView()
}
