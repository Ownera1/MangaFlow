//
//  ImageProcessingSettingsKey.swift
//  Aidoku
//
//  Created by 686udjie on 26/11/2025.
//

import Foundation

enum ImageProcessingSettingsKey {
    static func getProcessorSettingsKey() -> String {
        let crop = UserDefaults.standard.bool(forKey: "Reader.cropBorders")
        let downsample = UserDefaults.standard.bool(forKey: "Reader.downsampleImages")
        let upscale = UserDefaults.standard.bool(forKey: "Reader.upscaleImages")
        let maxHeight = UserDefaults.standard.integer(forKey: "Reader.upscaleMaxHeight")
        let quality = AppSettings.reader.upscaleQuality.get().rawValue
        let translate = UserDefaults.standard.bool(forKey: "Reader.translatePages")
        let targetLang = UserDefaults.standard.string(forKey: "Translation.targetLang") ?? "CHS"
        let serverURL = UserDefaults.standard.string(forKey: "Translation.serverURL") ?? ""
        return "\(crop)-\(downsample)-\(upscale)-\(maxHeight)-\(quality)-\(translate)-\(targetLang)-\(serverURL)"
    }
}
