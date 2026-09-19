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
    // Provide headers we attach to every request we make
    // this allows the eben device to know who this client is
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
                return "\(error)"
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
    let isDemo: Bool
    var services = ServiceState()
    
    init(
        address: String,
        isDemo: Bool = false,
        services: ServiceState = ServiceState()
    ) {
        self.address = address
        self.isDemo = isDemo
        self.services = services
    }
}

struct DeviceInfo {
    var model = ""
    var hostname = ""
    var serial = ""
    var revision = ""
    var kernelVersion = ""
    var cmdline = ""
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

struct ThrottleInfo: Decodable {
    let raw: Int
    let rawHex: String
    let undervoltageDetected: Bool
    let armFrequencyCapped: Bool
    let currentlyThrottled: Bool
    let softTemperatureLimitActive: Bool
    let undervoltageHasOccurred: Bool
    let armFrequencyCappingHasOccurred: Bool
    let throttlingHasOccurred: Bool
    let softTemperatureLimitHasOccurred: Bool
    
    enum CodingKeys: String, CodingKey {
        case raw
        case rawHex = "raw_hex"
        case undervoltageDetected = "undervoltage_detected"
        case armFrequencyCapped = "arm_frequency_capped"
        case currentlyThrottled = "currently_throttled"
        case softTemperatureLimitActive = "soft_temperature_limit_active"
        case undervoltageHasOccurred = "undervoltage_has_occurred"
        case armFrequencyCappingHasOccurred = "arm_frequency_capping_has_occurred"
        case throttlingHasOccurred = "throttling_has_occurred"
        case softTemperatureLimitHasOccurred = "soft_temperature_limit_has_occurred"
    }
}

struct ThrottleResponse: Decodable {
    let ok: Bool
    let data: ThrottleInfo?
    let error: String?
}

struct FanInfo: Decodable {
    let ok: Bool
    let control: String?
    let governor: String?
    let state: Int?
    let maxState: Int?
    let error: String?
    
    enum CodingKeys: String, CodingKey {
        case ok, control, governor, state, error
        case maxState = "max_state"
    }
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
        // there might be a better way to do this but for now this works
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
        async let cmdline = text("/kernel/cmdline")
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
        // on Eben devices (Raspberry Pi) here are what these corrospond to
        // cpu is the temperature that is reported by the kernel in sysfs
        // gpu is the temperature reported by a run of `vcgencmd measure_temp`
        // pretty sure that these 2 temps mean the same thing as all RPis use a System on Chip (SOC) design made by Broadcom (ARM processor, VideoCore GPU, etc in one chip)
        // pmic is the temperature reported by a run of `vcgencmd measure_temp pmic` and is the temp of the power management IC (?)
        // todo: pmic probably will fail on anything not a Pi 5 or later, this needs client + server handling
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
    
    func throttleInfo() async throws -> ThrottleInfo {
        let (data, _) = try await request("/power/throttle")
        let result = try JSONDecoder().decode(ThrottleResponse.self, from: data)
        guard result.ok, let throttleInfo = result.data else {
            throw EbenAPIError.api(result.error ?? "Unable to read throttling status.")
        }
        return throttleInfo
    }
    
    func fanInfo() async throws -> FanInfo {
        let (data, _) = try await request("/thermal/fan")
        return try decodeFanInfo(from: data)
    }
    
    func setFanGovernor(enabled: Bool) async throws -> FanInfo {
        let action = enabled ? "enable" : "disable"
        let (data, _) = try await request("/thermal/fan/governor/\(action)", method: "POST")
        let result = try decodeFanInfo(from: data)
        
        // The governor command response does not include the fan state range.
        // Fetch the complete snapshot so the controls remain internally consistent.
        return try await fanInfo()
    }
    
    func setFanState(_ state: Int) async throws -> FanInfo {
        let body = try JSONSerialization.data(withJSONObject: ["state": state])
        let (data, _) = try await request(
            "/thermal/fan/state",
            method: "POST",
            body: body,
            contentType: "application/json"
        )
        return try decodeFanInfo(from: data)
    }
    
    private func decodeFanInfo(from data: Data) throws -> FanInfo {
        let result = try JSONDecoder().decode(FanInfo.self, from: data)
        guard result.ok else {
            throw EbenAPIError.api(result.error ?? "Unable to read fan controls.")
        }
        return result
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
            // I'm not exactly sure what you would have to do to cause this, but safety is safety
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
        HostStatus(address: "10.12.194.1"), // usb gadget
        HostStatus(
            address: "0.0.0.0",
            isDemo: true,
            services: ServiceState(
                ssh: .online("Simulated"),
                http: .online("Simulated"),
                ios: .online("Simulated"),
                ranboo: .online("Demo")
            )
        )
    ]
    
    private var connections: [UUID: [CheckedService: NWConnection]] = [:]
    
    func checkAll() {
        for host in hosts {
            checkHost(id: host.id)
        }
    }
    
    func checkHost(id: UUID) {
        guard let index = hosts.firstIndex(where: { $0.id == id }) else { return }
        // for the demo device, skip it (it doesn't offer Ranboo services anyway)
        if hosts[index].isDemo {
            return
        }
        let address = hosts[index].address
        hosts[index].services = ServiceState(
            ssh: .checking,
            http: .checking,
            ios: .checking,
            ranboo: .checking
        )
        // check the 2 raw TCP services
        checkTCP(hostID: id, address: address, port: 22, service: .ssh)
        checkTCP(hostID: id, address: address, port: 8000, service: .http)
        
        // now start the 2 HTTP based tests
        Task {
            do {
                // check if port 8000 is HTTP and the HTTP stack can reach it
                try await EbenAPIClient(host: address).detectIfAvailableWithOS()
                update(id: id, service: .ios, status: .online("HTTP service exists"))
            } catch {
                update(id: id, service: .ios, status: .offline(Self.describe(error)))
            }
            
            do {
                // now check if this is actually ranboo
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
    @Published var throttle: ThrottleInfo?
    @Published var fan: FanInfo?
    @Published var vnc = VNCStatusViewData()
    @Published var previewImage: PlatformImage?
    @Published var isLoading = false
    @Published var isThrottledBecauseOfLPM = false
    @Published var isUpdatingFan = false
    @Published var statusMessage: String?
    @Published var errorMessage: String?
    
    private var api: EbenAPIClient { EbenAPIClient(host: host) }
    private var uptimeSecondsAtRefresh: TimeInterval?
    private var uptimeRefreshDate: Date?
    
    let host: String
    let isDemo: Bool
    
    init(host: String, isDemo: Bool) {
        self.host = host
        self.isDemo = isDemo
    }
    
    func loadDemoData() {
        info = DeviceInfo(
            model: "RanbooSwiftUI demo device",
            hostname: "demodevice",
            serial: "DEMO123456",
            revision: "d04170",
            kernelVersion: "Linux 6.6.51-v8+",
            uptime: "30.0"
        )
        
        memory = MemoryInfo(
            ok: true,
            totalMiB: 8192,
            usedMiB: 3200,
            freeMiB: 4992,
            error: nil
        )
        
        disks = [
            DiskInfo(
                device: "/dev/mmcblk0p2",
                mountpoint: "/",
                fstype: "ext4",
                opts: "rw,relatime",
                usage: DiskUsage(
                    total: 64_000_000_000,
                    used: 18_000_000_000,
                    free: 46_000_000_000,
                    percent: 28
                )
            ),
            DiskInfo(
                device: "/dev/sda1",
                mountpoint: "/mnt/media",
                fstype: "ext4",
                opts: "rw,relatime",
                usage: DiskUsage(
                    total: 500_000_000_000,
                    used: 210_000_000_000,
                    free: 290_000_000_000,
                    percent: 42
                )
            )
        ]
        
        thermal = ThermalInfo(
            cpu: 48.3,
            gpu: 46.8,
            pmic: 51.2
        )
        
        throttle = ThrottleInfo(
            raw: 0,
            rawHex: "0x0",
            undervoltageDetected: false,
            armFrequencyCapped: false,
            currentlyThrottled: false,
            softTemperatureLimitActive: false,
            undervoltageHasOccurred: false,
            armFrequencyCappingHasOccurred: false,
            throttlingHasOccurred: false,
            softTemperatureLimitHasOccurred: false
        )
        
        fan = FanInfo(
            ok: true,
            control: "governor",
            governor: "enabled",
            state: 2,
            maxState: 4,
            error: nil
        )
        
        vnc.description =
        "wayvnc PID XXXX, noVNC PID XXXX"
        
        setUptimeReference(from: info.uptime)
        
        errorMessage = nil
        
        statusMessage =
        "This is a UI demonstration and is not a real device. This mode is meant for mockups, demonstrations, and Apple's App Review. All actions are expected to throw errors."
    }
    
    func refresh() async {
        guard !isLoading else { return }
        isThrottledBecauseOfLPM = isLowPowerMode
        isLoading = true
        errorMessage = nil
        if isDemo {
            loadDemoData()
            isLoading = false
            return
        }
        defer { isLoading = false }
        
        do {
            let newInfo = try await api.deviceInfo()
            info = newInfo
            setUptimeReference(from: newInfo.uptime)
        } catch {
            errorMessage = HostChecker.describe(error)
        }
        
        await loadResources()
        await loadThermal()
        await loadThrottle()
        
        do {
            fan = try await api.fanInfo()
        } catch {
            errorMessage = HostChecker.describe(error)
        }
        
        do {
            applyVNCStatus(try await api.vncStatus())
        } catch {
            errorMessage = HostChecker.describe(error)
        }
    }
    
    func refreshThermal() async {
        guard !isLoading else { return }
        await loadThermal()
        await loadThrottle()
    }
    
    func refreshResources() async {
        guard !isLoading else { return }
        await loadResources()
    }
    
    private var isLowPowerMode: Bool {
#if os(iOS)
        ProcessInfo.processInfo.isLowPowerModeEnabled
#else
        false
#endif
    }
    
    func monitor() async {
        var thermalInterval: TimeInterval =
        isLowPowerMode ? 30 : 5
        
        var resourceInterval: TimeInterval =
        isLowPowerMode ? 60 : 10
        
        var nextThermalRefresh = Date().addingTimeInterval(5)
        var nextResourceRefresh = Date().addingTimeInterval(10)
        
        while !Task.isCancelled {
            let nextRefresh = min(nextThermalRefresh, nextResourceRefresh)
            do {
                try await Task.sleep(
                    for: .seconds(max(0, nextRefresh.timeIntervalSinceNow))
                )
            } catch {
                return
            }
            
            if Date() >= nextThermalRefresh {
                await refreshThermal()
                
                repeat {
                    nextThermalRefresh.addTimeInterval(thermalInterval)
                } while nextThermalRefresh <= Date()
            }
            
            if Date() >= nextResourceRefresh {
                await refreshResources()
                
                repeat {
                    nextResourceRefresh.addTimeInterval(resourceInterval)
                } while nextResourceRefresh <= Date()
            }
        }
    }
    
    private func loadThermal() async {
        do {
            thermal = try await api.thermal()
        } catch is CancellationError {
            return
        } catch {
            errorMessage = HostChecker.describe(error)
        }
    }
    
    private func loadThrottle() async {
        do {
            throttle = try await api.throttleInfo()
        } catch is CancellationError {
            return
        } catch {
            errorMessage = HostChecker.describe(error)
        }
    }
    
    private func loadResources() async {
        do {
            async let memoryRequest = api.memory()
            async let disksRequest = api.disks()
            let (newMemory, newDisks) = try await (memoryRequest, disksRequest)
            memory = newMemory
            disks = newDisks
        } catch is CancellationError {
            return
        } catch {
            errorMessage = HostChecker.describe(error)
        }
    }
    
    func uptime(at date: Date) -> TimeInterval? {
        guard let uptimeSecondsAtRefresh, let uptimeRefreshDate else { return nil }
        return max(0, uptimeSecondsAtRefresh + date.timeIntervalSince(uptimeRefreshDate))
    }
    
    func runVNC(_ path: String) async {
        await perform("VNC command completed") {
            let response = try await api.command(path)
            applyVNCStatus(response)
        }
    }
    
    func setFanGovernor(enabled: Bool) async {
        guard !isUpdatingFan else { return }
        isUpdatingFan = true
        defer { isUpdatingFan = false }
        
        await perform("Fan governor \(enabled ? "enabled" : "disabled")") {
            fan = try await api.setFanGovernor(enabled: enabled)
        }
    }
    
    func setFanState(_ state: Int) async {
        guard !isUpdatingFan, fan?.control == "userspace", fan?.state != state else { return }
        isUpdatingFan = true
        defer { isUpdatingFan = false }
        
        await perform("Fan state set to \(state)") {
            fan = try await api.setFanState(state)
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
    
    func restartHyprland() async {
        await perform("Hyprland restarted") {
            _ = try await api.command("/hyprland/restart")
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
                // for example, Swift Playgrounds packages (.swiftpm) and macOS applications (.app) are actually directories and will error
                // iCloud Drive on the web handles this by putting said files in .ZIP containers when downloaded, perhaps we do something similar?
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
        // this isn't the *best* way to do this but it's certanly a way
        if let pid = response.wayvncPID { parts.append("wayvnc PID \(pid)") }
        if let pid = response.novncPID { parts.append("noVNC PID \(pid)") }
        if response.websocket == true { parts.append("WebSocket enabled") }
        // this might break if somehow you end up with PIDs and yet it claims it's stopped?
        if response.stopped == true { parts.append("Stopped") }
        vnc.description = parts.isEmpty ? "No tracked VNC process" : parts.joined(separator: ", ")
    }
    
    private func setUptimeReference(from rawValue: String) {
        guard let secondsText = rawValue.split(separator: " ").first,
              let seconds = TimeInterval(secondsText) else {
            uptimeSecondsAtRefresh = nil
            uptimeRefreshDate = nil
            return
        }
        uptimeSecondsAtRefresh = seconds
        uptimeRefreshDate = Date()
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
                DeviceDetailView(
                    host: selectedHost.address,
                    isDemo: selectedHost.isDemo
                )
                .id(selectedHost.id)
            } else {
                DevicePlaceholderView()
            }
        }
        .navigationSplitViewStyle(.balanced)
        .task {
            checker.checkAll()
        }
        .onReceive(checker.$hosts) { hosts in
            // this is commented out as we don't want to autofocus on a host, especially if more than 1 interface is enabled on Eben
            // guard selectedHostID == nil else { return }
            // selectedHostID = hosts.first(where: { $0.services.ranboo.isOnline })?.id
        }
    }
}


struct DevicePlaceholderView: View {
    @State private var randomDescriptionDevJokes = [
        "So grab a plate, have a taste!",
        "Butcher Vanity是一个爆炸物",
        "https://www.youtube.com/channel/UCKQ-wNdh0kO5qnpPfXa2hjQ"
    ].randomElement()!
    
    static var deviceType: String {
#if os(visionOS)
        return "Apple Vision"
#elseif os(iOS)
        switch UIDevice.current.userInterfaceIdiom {
        case .phone: return "iPhone"
        case .pad: return "iPad"
        case .carPlay: return "CarPlay"
        default: return "Unknown iOS device)"
        }
#elseif os(macOS)
        return "Mac"
#elseif os(tvOS)
        return "Apple TV" // How??
#elseif os(watchOS)
        return "Apple Watch" // How??
#else
        return "Unknown"
#endif
    }
    
    // for production
    @State private var randomDescription = [
        "Make sure your Project Eben and \(deviceType) are connected together.",
    ].randomElement()!
    
    var body: some View {
        ContentUnavailableView(
            "Select a Device",
            systemImage: "desktopcomputer",
            // description: Text("青葉真司をファッ")
            description: Text(randomDescriptionDevJokes)
        )
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
    @State private var selectedDate = Date()
    @State private var isConfirmingHyprlandRestart = false
    @State private var pendingIronmouseAction: IronmouseAction?
    @State private var isShowingFileImporter = false
    @State private var selectedPhotoItem: PhotosPickerItem?
    @Environment(\.openURL) private var openURL
    
    init(host: String, isDemo: Bool) {
        _model = StateObject(
            wrappedValue: DeviceViewModel(
                host: host,
                isDemo: isDemo
            )
        )
    }
    
    var body: some View {
        configuredList
    }
    
    private var configuredList: some View {
        deviceList
            .navigationTitle(navigationTitle)
#if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
#endif
            .toolbar { refreshToolbar }
            .task { await refreshAndMonitor() }
            .fileImporter(
                isPresented: $isShowingFileImporter,
                allowedContentTypes: [.item],
                allowsMultipleSelection: false,
                onCompletion: handleFileImport
            )
            .onChange(of: selectedPhotoItem) { item in
                handlePhotoSelection(item)
            }
            .modifier(DeviceConfirmationDialogs(
                model: model,
                pendingPowerAction: $pendingPowerAction,
                isConfirmingHyprlandRestart: $isConfirmingHyprlandRestart,
                pendingIronmouseAction: $pendingIronmouseAction
            ))
    }
    
    private var navigationTitle: String {
        let hostname = model.info.hostname
        return hostname.isEmpty ? model.host : hostname
    }
    
    @ToolbarContentBuilder
    private var refreshToolbar: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            Button(action: refreshDevice) {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
        }
    }
    
    private var deviceList: some View {
        List {
            primarySections
            controlSections
            feedbackSections
        }
    }
    
    @ViewBuilder
    private var primarySections: some View {
        errorSection
        loadingSection
        deviceSection
        osSection
        memorySection
        disksSection
        thermalSection
        throttlingSection
    }
    
    @ViewBuilder
    private var controlSections: some View {
        FanControlsView(model: model)
        remoteDesktopSection
        sendFileSection
        ironmouseAPSection
        ironmouseEthernetSection
        timeSetSection
        powerSection
    }
    
    @ViewBuilder
    private var feedbackSections: some View {
        debugSection
        statusSection
    }
    
    @ViewBuilder
    private var loadingSection: some View {
        if model.isLoading {
            Section { ProgressView("Loading device data...") }
        }
    }
    
    private var deviceSection: some View {
        Section("Device") {
            LabeledContent("Hostname", value: display(model.info.hostname))
            LabeledContent("Model", value: display(model.info.model))
            LabeledContent("Serial", value: display(model.info.serial))
            LabeledContent("Revision", value: display(model.info.revision))
            uptimeRow
        }
    }
    
    private var osSection: some View {
        Section("Operating System") {
            LabeledContent("Kernel", value: display(model.info.kernelVersion))
            LabeledContent("Cmdline", value: display(model.info.cmdline))
        }
    }
    private var uptimeRow: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let value = formattedUptime(at: context.date)
            LabeledContent("Uptime", value: value)
        }
    }
    
    @ViewBuilder
    private var memorySection: some View {
        Section("Memory") {
            if let memory = model.memory,
               let used = memory.usedMiB,
               let total = memory.totalMiB,
               total > 0 {
                let usedText = format(used)
                let totalText = format(total)
                let usageText = "\(usedText) / \(totalText) MiB"
                LabeledContent("Used", value: usageText)
                ProgressView(value: used, total: total)
                if let free = memory.freeMiB {
                    let freeText = "\(format(free)) MiB"
                    LabeledContent("Free", value: freeText)
                }
            } else {
                Text("No memory data").foregroundStyle(.secondary)
            }
        }
    }
    
    @ViewBuilder
    private var disksSection: some View {
        Section("Disks") {
            if model.disks.isEmpty {
                Text("No disk data").foregroundStyle(.secondary)
            } else {
                ForEach(model.disks) { disk in
                    DiskUsageView(disk: disk)
                }
            }
        }
    }
    
    private var thermalSection: some View {
        Section("Thermals") {
            temperatureRow("CPU", value: model.thermal.cpu)
            temperatureRow("GPU", value: model.thermal.gpu)
            temperatureRow("PMIC", value: model.thermal.pmic)
        }
    }
    
    @ViewBuilder
    private var throttlingSection: some View {
        Section("Throttling") {
            if let throttle = model.throttle {
                LabeledContent("Currently throttled", value: yesNo(throttle.currentlyThrottled))
                LabeledContent("Undervoltage detected", value: yesNo(throttle.undervoltageDetected))
                LabeledContent("ARM frequency capped", value: yesNo(throttle.armFrequencyCapped))
                LabeledContent("Soft temperature limit", value: yesNo(throttle.softTemperatureLimitActive))
                LabeledContent("Throttling has occurred", value: yesNo(throttle.throttlingHasOccurred))
                LabeledContent("Undervoltage has occurred", value: yesNo(throttle.undervoltageHasOccurred))
                LabeledContent("Frequency capping has occurred", value: yesNo(throttle.armFrequencyCappingHasOccurred))
                LabeledContent("Soft temperature limit has occurred", value: yesNo(throttle.softTemperatureLimitHasOccurred))
                LabeledContent("Raw status", value: throttle.rawHex)
            } else {
                Text("No throttling data").foregroundStyle(.secondary)
            }
        }
    }
    
    private var remoteDesktopSection: some View {
        Section("Remote Desktop") {
            Text(model.vnc.description).foregroundStyle(.secondary)
            Button("Restart Hyprland", role: .destructive) {
                isConfirmingHyprlandRestart = true
            }
            vncButton("Start VNC", path: "/vnc/start")
            vncButton("Start VNC with WebSocket", path: "/vnc/start/websocket")
            vncButton("Start noVNC", path: "/vnc/start/novnc")
            vncButton("Stop VNC", path: "/vnc/stop", role: .destructive)
            vncButton("Stop noVNC", path: "/vnc/stop/novnc", role: .destructive)
            Button("Load Preview", action: loadPreview)
            Button("Launch noVNC", action: openNoVNC)
            previewView
        }
    }
    
    private var timeSetSection: some View {
        Section("Time Set") {
            Text("In most cases you shouldn't need to use this (and it probably won't work) but if you have automatic time sync off, this may be useful")
            DatePicker(
                "Date and Time",
                selection: $selectedDate,
                displayedComponents: [.date, .hourAndMinute]
            )
            
            Button("Set Time From Manually") {
                // TODO: Send selectedDate to API
                print("Set device time to \(selectedDate)")
            }
            
            Button("Set Time From Sys Clock") {
                // TODO: Send selectedDate to API
                print("Set device time to sys clock")
            }
        }
    }
    
    @ViewBuilder
    private var previewView: some View {
        if let image = model.previewImage {
            platformImage(image)
                .resizable()
                .scaledToFit()
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }
    
    private var sendFileSection: some View {
        // todo: add option for using taking a picture in-app and sending that on iOS/iPadOS
        Section("Send File") {
            PhotosPicker(selection: $selectedPhotoItem, matching: .any(of: [.images, .videos])) {
                Label("Choose from Photos Library", systemImage: "photo.on.rectangle")
            }
            Button(action: showFileImporter) {
                Label("Choose from Files", systemImage: "folder")
            }
            Text("The selected file will be uploaded to ~/Downloads using its original filename.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
    
    private var ironmouseAPSection: some View {
        Section("Ironmouse AP") {
            ForEach(IronmouseAction.apActions) { action in
                Button(action.title, role: action.role) {
                    pendingIronmouseAction = action
                }
            }
        }
    }
    
    private var ironmouseEthernetSection: some View {
        Section("Ironmouse Eth") {
            ForEach(IronmouseAction.ethernetActions) { action in
                Button(action.title, role: action.role) {
                    pendingIronmouseAction = action
                }
            }
        }
    }
    
    private var powerSection: some View {
        Section("Power") {
            ForEach(PowerAction.allCases) { action in
                Button(action.title, role: action.role) {
                    pendingPowerAction = action
                }
            }
        }
    }
    
    private var debugSection: some View {
        let text = "IP: \(model.host)\nisLoading: \(model.isLoading)\nLPM throttled: \(model.isThrottledBecauseOfLPM)"
        return Section("Debug") {
            Text(text).monospaced()
            Button("Go to Ranboo WebUI", action: openRanbooWebUI)
        }
    }
    
    @ViewBuilder
    private var statusSection: some View {
        if let message = model.statusMessage {
            Section { Text(message).foregroundStyle(.green) }
        }
    }
    
    @ViewBuilder
    private var errorSection: some View {
        if let error = model.errorMessage {
            Section("Networking Error") {
                Text(error)
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
            }
        }
    }
    
    private func vncButton(_ title: String, path: String, role: ButtonRole? = nil) -> some View {
        Button(title, role: role) {
            Task { await model.runVNC(path) }
        }
    }
    
    private func yesNo(_ value: Bool) -> String {
        value ? "Yes" : "No"
    }
    
    private func refreshDevice() {
        Task { await model.refresh() }
    }
    
    private func refreshAndMonitor() async {
        await model.refresh()
        await model.monitor()
    }
    
    private func showFileImporter() {
        isShowingFileImporter = true
    }
    
    private func loadPreview() {
        Task { await model.loadPreview() }
    }
    
    private func openRanbooWebUI() {
        openURLForPort(8000, path: "")
    }
    
    private func openNoVNC() {
        openURLForPort(6080, path: "/vnc.html")
    }
    
    private func openURLForPort(_ port: Int, path: String) {
        let address = "http://\(model.host):\(port)\(path)"
        guard let url = URL(string: address) else { return }
        openURL(url)
    }
    
    private func handleFileImport(_ result: Result<[URL], Error>) {
        switch result {
        case .success(let urls):
            guard let url = urls.first else { return }
            Task { await model.sendFile(at: url) }
        case .failure(let error):
            model.errorMessage = HostChecker.describe(error)
        }
    }
    
    private func handlePhotoSelection(_ item: PhotosPickerItem?) {
        guard let item else { return }
        Task { await sendPhoto(item) }
    }
    
    private func sendPhoto(_ item: PhotosPickerItem) async {
        do {
            guard let data = try await item.loadTransferable(type: Data.self) else {
                throw EbenAPIError.api("The selected Photos item could not be loaded.")
            }
            let maximumUploadSize = 512 * 1024 * 1024
            guard data.count <= maximumUploadSize else {
                throw EbenAPIError.api("The selected file is larger than the 512 MB upload limit.")
            }
            let originalName = originalPhotoFilename(for: item)
            let filename = originalName ?? fallbackPhotoFilename(for: item)
            await model.sendFile(data: data, originalFilename: filename)
        } catch {
            model.errorMessage = HostChecker.describe(error)
        }
        selectedPhotoItem = nil
    }
    
    private func originalPhotoFilename(for item: PhotosPickerItem) -> String? {
        guard let identifier = item.itemIdentifier else { return nil }
        let identifiers = [identifier]
        let result = PHAsset.fetchAssets(withLocalIdentifiers: identifiers, options: nil)
        guard let asset = result.firstObject else { return nil }
        let resources = PHAssetResource.assetResources(for: asset)
        let finalName = resources.first?.originalFilename
        print("Saving as \(String(describing: finalName))")
        return finalName
    }
    
    private func fallbackPhotoFilename(for item: PhotosPickerItem) -> String {
        let type = item.supportedContentTypes.first
        let extensionPart = type?.preferredFilenameExtension.map { ".\($0)" } ?? ""
        let identifier = UUID().uuidString
        let finalName = "Photo-\(identifier)\(extensionPart)"
        print("Could not get the original Photos filename; using \(finalName)")
        return finalName
    }
    
    private func display(_ value: String) -> String {
        value.isEmpty ? "Unknown" : value
    }
    
    private func format(_ value: Double) -> String {
        value.formatted(.number.precision(.fractionLength(0...1)))
    }
    
    private func formattedUptime(at date: Date) -> String {
        guard let uptime = model.uptime(at: date) else {
            return display(model.info.uptime)
        }
        let totalSeconds = Int(uptime.rounded(.down))
        let days = totalSeconds / 86_400
        let hours = totalSeconds % 86_400 / 3_600
        let minutes = totalSeconds % 3_600 / 60
        let seconds = totalSeconds % 60
        if days > 0 { return "\(days)d \(hours)h \(minutes)m \(seconds)s" }
        if hours > 0 { return "\(hours)h \(minutes)m \(seconds)s" }
        return "\(minutes)m \(seconds)s"
    }
    
    @ViewBuilder
    private func temperatureRow(_ label: String, value: Double?) -> some View {
        if let value {
            // todo: use the temperature units set in your OS locale settings
            let temperatureText = "\(format(value)) °C"
            let tint = temperatureTint(for: value)
            LabeledContent(label, value: temperatureText)
            // "In fact, Raspberry Pi devices have been tested to well over 120degree C with no problems"
            // this seems to imply that Raspberry Pi has no thermal shutdown (?)
            Gauge(value: value, in: 0...120) { EmptyView() }.tint(tint)
        } else {
            LabeledContent(label, value: "Unknown")
        }
    }
    
    private func temperatureTint(for value: Double) -> Color {
        // Cooler than normal operating temperature
        if value < 40 { return .cyan }
        
        // Warning levels
        // Raspberry Pi throttles above 85C
        if value >= 85 { return .red }
        if value >= 70 { return .orange }
        if value >= 65 { return .yellow }
        
        // Normal operating range
        return .green
    }
    
    private func platformImage(_ image: PlatformImage) -> Image {
#if os(iOS)
        return Image(uiImage: image)
#elseif os(macOS)
        return Image(nsImage: image)
#endif
    }
}

private struct DiskUsageView: View {
    let disk: DiskInfo
    
    var body: some View {
        let usedText = formatBytes(disk.usage.used)
        let totalText = formatBytes(disk.usage.total)
        let usageText = "\(usedText) / \(totalText)"
        let progressTotal = max(disk.usage.total, 1)
        VStack(alignment: .leading, spacing: 8) {
            LabeledContent(disk.mountpoint, value: disk.device).font(.headline)
            LabeledContent("Used", value: usageText)
            ProgressView(value: disk.usage.used, total: progressTotal)
            LabeledContent("Free", value: formatBytes(disk.usage.free))
            LabeledContent("Filesystem", value: display(disk.fstype))
        }
        .padding(.vertical, 4)
    }
    
    private func display(_ value: String) -> String {
        value.isEmpty ? "Unknown" : value
    }
    
    private func formatBytes(_ value: Double) -> String {
        let bytes = Int64(value.rounded())
        return ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }
}

private struct DeviceConfirmationDialogs: ViewModifier {
    @ObservedObject var model: DeviceViewModel
    @Binding var pendingPowerAction: PowerAction?
    @Binding var isConfirmingHyprlandRestart: Bool
    @Binding var pendingIronmouseAction: IronmouseAction?
    
    func body(content: Content) -> some View {
        content
            .confirmationDialog(
                powerDialogTitle,
                isPresented: powerDialogBinding,
                titleVisibility: .visible,
                actions: powerDialogActions,
                message: immediateActionMessage
            )
            .confirmationDialog(
                "Restart Hyprland on \(model.host)?",
                isPresented: $isConfirmingHyprlandRestart,
                titleVisibility: .visible,
                actions: hyprlandDialogActions,
                message: hyprlandMessage
            )
            .confirmationDialog(
                ironmouseDialogTitle,
                isPresented: ironmouseDialogBinding,
                titleVisibility: .visible,
                actions: ironmouseDialogActions,
                message: immediateActionMessage
            )
    }
    
    private var powerDialogTitle: String {
        guard let action = pendingPowerAction else { return "Power action" }
        return "\(action.title) \(model.host)?"
    }
    
    private var ironmouseDialogTitle: String {
        guard let action = pendingIronmouseAction else { return "Ironmouse action" }
        return "\(action.title) Ironmouse \(action.targetTitle)?"
    }
    
    private var powerDialogBinding: Binding<Bool> {
        Binding(
            get: { pendingPowerAction != nil },
            set: { isPresented in if !isPresented { pendingPowerAction = nil } }
        )
    }
    
    private var ironmouseDialogBinding: Binding<Bool> {
        Binding(
            get: { pendingIronmouseAction != nil },
            set: { isPresented in if !isPresented { pendingIronmouseAction = nil } }
        )
    }
    
    @ViewBuilder
    private func powerDialogActions() -> some View {
        if let action = pendingPowerAction {
            Button(action.title, role: action.role) {
                pendingPowerAction = nil
                Task { await model.power(action.path) }
            }
        }
        Button("Cancel", role: .cancel) { pendingPowerAction = nil }
    }
    
    @ViewBuilder
    private func hyprlandDialogActions() -> some View {
        Button("Restart Hyprland", role: .destructive) {
            Task { await model.restartHyprland() }
        }
        Button("Cancel", role: .cancel) {}
    }
    
    @ViewBuilder
    private func ironmouseDialogActions() -> some View {
        if let action = pendingIronmouseAction {
            Button(action.title, role: action.role) {
                pendingIronmouseAction = nil
                Task { await model.power(action.path) }
            }
        }
        Button("Cancel", role: .cancel) { pendingIronmouseAction = nil }
    }
    
    private func immediateActionMessage() -> some View {
        Text("This command affects the remote device immediately.")
    }
    
    private func hyprlandMessage() -> some View {
        Text("The desktop compositor and active remote desktop session may disconnect briefly.")
    }
}

struct FanControlsView: View {
    @ObservedObject var model: DeviceViewModel
    @State private var selectedState = 0.0
    @State private var isEditingSlider = false
    
    private var isGovernorEnabled: Bool {
        model.fan?.governor == "enabled"
    }
    
    private var canControlManually: Bool {
        model.fan?.control == "userspace" && !model.isUpdatingFan
    }
    
    var body: some View {
        Section("Fan") {
            if let fan = model.fan {
                Toggle(
                    "Automatic governor",
                    isOn: Binding(
                        get: { isGovernorEnabled },
                        set: { enabled in
                            Task { await model.setFanGovernor(enabled: enabled) }
                        }
                    )
                )
                .disabled(model.isUpdatingFan || fan.governor == nil)
                
                LabeledContent("Control", value: controlDescription(fan.control))
                LabeledContent("Current state", value: stateDescription(fan))
                
                if let maxState = fan.maxState, maxState > 0 {
                    VStack(alignment: .leading, spacing: 8) {
                        LabeledContent(
                            "Manual state",
                            value: "\(Int(selectedState.rounded())) / \(maxState)"
                        )
                        Slider(
                            value: $selectedState,
                            in: 0...Double(maxState),
                            step: 1
                        ) { editing in
                            isEditingSlider = editing
                            if !editing {
                                let state = Int(selectedState.rounded())
                                Task { await model.setFanState(state) }
                            }
                        }
                        .disabled(!canControlManually)
                    }
                    
                    if isGovernorEnabled {
                        Text("Disable the automatic governor to set the fan state manually.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    Text("The server did not report a usable fan state range.")
                        .foregroundStyle(.secondary)
                }
                
                if model.isUpdatingFan {
                    ProgressView("Updating fan...")
                }
            } else {
                Text("No fan data")
                    .foregroundStyle(.secondary)
            }
        }
        .onChange(of: model.fan?.state, initial: true) { _, state in
            guard !isEditingSlider, let state else { return }
            selectedState = Double(state)
        }
    }
    
    private func controlDescription(_ control: String?) -> String {
        switch control {
        case "governor": return "Automatic governor"
        case "userspace": return "Manual"
        default: return "Unknown"
        }
    }
    
    private func stateDescription(_ fan: FanInfo) -> String {
        guard let state = fan.state else { return "Unknown" }
        guard let maxState = fan.maxState else { return "\(state)" }
        return "\(state) / \(maxState)"
    }
}

enum PowerAction: String, CaseIterable, Identifiable {
    case logout, restart, sleep, hibernate, poweroff
    
    var id: String { rawValue }
    var path: String { "/power/\(rawValue)" }
    
    var title: String {
        switch self {
        case .logout: return "Log Out"
        case .restart: return "Restart"
        case .sleep: return "Sleep"
        case .hibernate: return "Hibernate"
        case .poweroff: return "Power Off"
        }
    }
    
    var role: ButtonRole? {
        switch self {
        case .restart, .poweroff, .logout: return .destructive
        case .sleep, .hibernate: return nil
        }
    }
}
enum IronmouseAction: String, Identifiable {
    case enableAP
    case disableAP
    case enableEthernet
    case disableEthernet
    
    static let apActions: [IronmouseAction] = [.enableAP, .disableAP]
    static let ethernetActions: [IronmouseAction] = [.enableEthernet, .disableEthernet]
    
    var id: String { rawValue }
    
    var path: String {
        switch self {
        case .enableAP: return "/ironmouse/ap/enable"
        case .disableAP: return "/ironmouse/ap/disable"
        case .enableEthernet: return "/ironmouse/eth/enable"
        case .disableEthernet: return "/ironmouse/eth/disable"
        }
    }
    
    var title: String {
        switch self {
        case .enableAP, .enableEthernet: return "Enable"
        case .disableAP, .disableEthernet: return "Disable"
        }
    }
    
    var targetTitle: String {
        switch self {
        case .enableAP, .disableAP: return "AP"
        case .enableEthernet, .disableEthernet: return "Ethernet"
        }
    }
    
    var role: ButtonRole? { nil }
}

#Preview {
    ContentView()
}
