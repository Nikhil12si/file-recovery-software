/**
 * mockData.js - generated from contracts/mocks/drives.json (keep in sync via contracts/README.md).
 */
export const MOCK_DRIVES = {
  "schema_version": "1.0",
  "generated_at": "2026-10-05T10:49:46Z",
  "engine_version": "1.0.0-mock",
  "elevation": {
    "elevated": true,
    "elevation_type": "full",
    "in_administrators_group": true,
    "error": null
  },
  "physical_drives": [
    {
      "index": 0,
      "device_path": "\\\\.\\PhysicalDrive0",
      "model": "Samsung SSD 980 PRO 512GB",
      "serial": "MOCK0000",
      "bus_type": "NVMe",
      "media_type": "SSD",
      "removable": false,
      "size_bytes": 512110190592,
      "bytes_per_sector": 512,
      "physical_bytes_per_sector": 512,
      "total_sectors": 1000215216,
      "partition_style": "GPT",
      "accessible": true,
      "error": null,
      "partitions": [
        {
          "number": 1,
          "offset_bytes": 1048576,
          "length_bytes": 511000000000,
          "style": "GPT",
          "type_label": "Basic data partition",
          "mounted_letter": "C:",
          "source_spec": "\\\\.\\PhysicalDrive0@p1"
        }
      ]
    },
    {
      "index": 1,
      "device_path": "\\\\.\\PhysicalDrive1",
      "model": "WDC WD20EZRZ-00Z5HB0",
      "serial": "MOCK0001",
      "bus_type": "SATA",
      "media_type": "HDD",
      "removable": false,
      "size_bytes": 2000398934016,
      "bytes_per_sector": 512,
      "physical_bytes_per_sector": 4096,
      "total_sectors": 3907029168,
      "partition_style": "GPT",
      "accessible": true,
      "error": null,
      "partitions": [
        {
          "number": 1,
          "offset_bytes": 1048576,
          "length_bytes": 1000202000000,
          "style": "GPT",
          "type_label": "Basic data partition",
          "mounted_letter": "D:",
          "source_spec": "\\\\.\\PhysicalDrive1@p1"
        },
        {
          "number": 2,
          "offset_bytes": 1000204000000,
          "length_bytes": 999000000000,
          "style": "GPT",
          "type_label": "Basic data partition",
          "mounted_letter": "F:",
          "source_spec": "\\\\.\\PhysicalDrive1@p2"
        },
        {
          "number": 3,
          "offset_bytes": 1999300000000,
          "length_bytes": 1000000000,
          "style": "GPT",
          "type_label": "Basic data partition",
          "mounted_letter": null,
          "source_spec": "\\\\.\\PhysicalDrive1@p3"
        }
      ]
    },
    {
      "index": 2,
      "device_path": "\\\\.\\PhysicalDrive2",
      "model": "Seagate Expansion Desk",
      "serial": "MOCK0002",
      "bus_type": "USB",
      "media_type": "HDD",
      "removable": true,
      "size_bytes": 32017047552,
      "bytes_per_sector": 512,
      "physical_bytes_per_sector": 4096,
      "total_sectors": 62533296,
      "partition_style": "GPT",
      "accessible": true,
      "error": null,
      "partitions": [
        {
          "number": 1,
          "offset_bytes": 1048576,
          "length_bytes": 31000000000,
          "style": "GPT",
          "type_label": "Basic data partition",
          "mounted_letter": "E:",
          "source_spec": "\\\\.\\PhysicalDrive2@p1"
        }
      ]
    }
  ],
  "volumes": [
    {
      "letter": "C:",
      "root_path": "C:\\",
      "device_path": "\\\\.\\C:",
      "volume_guid_path": "\\\\?\\Volume{00000000-0000-0000-0000-000000000067}\\",
      "nt_device_name": "\\Device\\HarddiskVolume3",
      "label": "Windows",
      "filesystem": "NTFS",
      "drive_type": "fixed",
      "total_bytes": 511000000000,
      "free_bytes": 120000000000,
      "bytes_per_sector": 512,
      "cluster_size": 4096,
      "total_sectors": 998046875,
      "physical_drive_index": 0,
      "partition_offset_bytes": 1048576,
      "is_system_volume": true,
      "accessible": true,
      "error": null,
      "source_spec": "C:"
    },
    {
      "letter": "D:",
      "root_path": "D:\\",
      "device_path": "\\\\.\\D:",
      "volume_guid_path": "\\\\?\\Volume{00000001-0000-0000-0000-000000000068}\\",
      "nt_device_name": "\\Device\\HarddiskVolume4",
      "label": "Data",
      "filesystem": "NTFS",
      "drive_type": "fixed",
      "total_bytes": 1000202000000,
      "free_bytes": 410000000000,
      "bytes_per_sector": 512,
      "cluster_size": 4096,
      "total_sectors": 1953519531,
      "physical_drive_index": 1,
      "partition_offset_bytes": 1048576,
      "is_system_volume": false,
      "accessible": true,
      "error": null,
      "source_spec": "D:"
    },
    {
      "letter": "E:",
      "root_path": "E:\\",
      "device_path": "\\\\.\\E:",
      "volume_guid_path": "\\\\?\\Volume{00000002-0000-0000-0000-000000000069}\\",
      "nt_device_name": "\\Device\\HarddiskVolume5",
      "label": "USB_BACKUP",
      "filesystem": "FAT32",
      "drive_type": "removable",
      "total_bytes": 31000000000,
      "free_bytes": 9000000000,
      "bytes_per_sector": 512,
      "cluster_size": 32768,
      "total_sectors": 60546875,
      "physical_drive_index": 2,
      "partition_offset_bytes": 1048576,
      "is_system_volume": false,
      "accessible": true,
      "error": null,
      "source_spec": "E:"
    },
    {
      "letter": "F:",
      "root_path": "F:\\",
      "device_path": "\\\\.\\F:",
      "volume_guid_path": "\\\\?\\Volume{00000001-0000-0000-0000-000000000070}\\",
      "nt_device_name": "\\Device\\HarddiskVolume6",
      "label": "Archive",
      "filesystem": "NTFS",
      "drive_type": "fixed",
      "total_bytes": 999000000000,
      "free_bytes": 700000000000,
      "bytes_per_sector": 512,
      "cluster_size": 4096,
      "total_sectors": 1951171875,
      "physical_drive_index": 1,
      "partition_offset_bytes": 1000204000000,
      "is_system_volume": false,
      "accessible": true,
      "error": null,
      "source_spec": "F:"
    }
  ]
};
