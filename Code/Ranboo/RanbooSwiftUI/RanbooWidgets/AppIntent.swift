import AppIntents

struct ConfigurationAppIntent: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "Temperature Widget"
    static var description = IntentDescription("Show temperatures from a Ranboo device.")

    @Parameter(title: "Device address", description: "The device hostname or IP address (port 8000 is used).")
    var deviceAddress: String

    @Parameter(title: "Temperature sensor", default: .cpu)
    var sensor: TemperatureSensor
}

enum TemperatureSensor: String, AppEnum {
    case cpu
    case gpu
    case pmic

    static var typeDisplayRepresentation = TypeDisplayRepresentation(name: "Temperature sensor")
    static var caseDisplayRepresentations: [TemperatureSensor: DisplayRepresentation] = [
        .cpu: "CPU",
        .gpu: "GPU",
        .pmic: "PMIC"
    ]
}
