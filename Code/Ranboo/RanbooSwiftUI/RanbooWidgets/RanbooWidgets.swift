import Foundation
import WidgetKit
import SwiftUI

struct TemperatureEntry: TimelineEntry {
    let date: Date
    let deviceAddress: String
    let sensor: TemperatureSensor
    let temperature: Double?
    let error: String?
}

struct TemperatureProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> TemperatureEntry {
        TemperatureEntry(date: .now, deviceAddress: "eben-device", sensor: .cpu, temperature: 48.3, error: nil)
    }

    func snapshot(for configuration: ConfigurationAppIntent, in context: Context) async -> TemperatureEntry {
        await makeEntry(configuration)
    }

    func timeline(for configuration: ConfigurationAppIntent, in context: Context) async -> Timeline<TemperatureEntry> {
        let entry = await makeEntry(configuration)
        let nextUpdate = Calendar.current.date(byAdding: .minute, value: 15, to: .now) ?? .now.addingTimeInterval(900)
        return Timeline(entries: [entry], policy: .after(nextUpdate))
    }

    private func makeEntry(_ configuration: ConfigurationAppIntent) async -> TemperatureEntry {
        let host = configuration.deviceAddress.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !host.isEmpty else {
            return TemperatureEntry(date: .now, deviceAddress: "", sensor: configuration.sensor, temperature: nil, error: "Set a device address")
        }

        do {
            let value = try await fetchTemperature(host: host, sensor: configuration.sensor)
            return TemperatureEntry(date: .now, deviceAddress: host, sensor: configuration.sensor, temperature: value, error: nil)
        } catch {
            return TemperatureEntry(date: .now, deviceAddress: host, sensor: configuration.sensor, temperature: nil, error: "Unable to connect")
        }
    }

    private func fetchTemperature(host: String, sensor: TemperatureSensor) async throws -> Double {
        var components = URLComponents()
        components.scheme = "http"
        components.host = host
        components.port = 8000
        components.path = "/thermal/\(sensor.rawValue)"
        guard let url = components.url else { throw URLError(.badURL) }

        var request = URLRequest(url: url, timeoutInterval: 8)
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse, (200...299).contains(response.statusCode),
              let text = String(data: data, encoding: .utf8),
              let value = Double(text.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            throw URLError(.cannotParseResponse)
        }
        return value
    }
}

struct TemperatureWidgetView: View {
    let entry: TemperatureEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "thermometer.medium")
                    .foregroundStyle(.orange)
                Text(entry.sensor.rawValue.uppercased())
                    .font(.headline)
                Spacer(minLength: 0)
            }

            if let temperature = entry.temperature {
                Text(temperature, format: .number.precision(.fractionLength(1)))
                    .font(.system(size: 38, weight: .semibold, design: .rounded))
                    .minimumScaleFactor(0.7)
                    .lineLimit(1)
                    .contentTransition(.numericText())
                Text("°C")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Text(entry.error ?? "No reading")
                    .font(.headline)
                    .foregroundStyle(.secondary)
                Text(entry.deviceAddress)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
            if entry.temperature != nil {
                Text(entry.deviceAddress)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .containerBackground(.fill.tertiary, for: .widget)
    }
}

struct RanbooTemperatureWidget: Widget {
    let kind = "RanbooTemperatureWidget"

    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: kind, intent: ConfigurationAppIntent.self, provider: TemperatureProvider()) { entry in
            TemperatureWidgetView(entry: entry)
        }
        .configurationDisplayName("Device Temperature")
        .description("See a device's CPU, GPU, or PMIC temperature.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

#Preview(as: .systemSmall) {
    RanbooTemperatureWidget()
} timeline: {
    TemperatureEntry(date: .now, deviceAddress: "10.42.0.1", sensor: .cpu, temperature: 48.3, error: nil)
}
