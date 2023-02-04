/*
 SensorAlarmChart

 This Source Code Form is subject to the terms of the Mozilla Public
 License, v. 2.0. If a copy of the MPL was not distributed with this
 file, You can obtain one at http://mozilla.org/MPL/2.0/.

 Copyright © 2022 Charles E. Vejnar
*/

import { addLoadEvent } from './utils.js'

import { Chart, registerables } from './chart.esm.js'
Chart.register(...registerables)

Chart.defaults.font.size = 20

const colors = ['rgb(255, 99, 132)', 'rgb(54, 162, 235)', 'rgb(75, 192, 192)', 'rgb(255, 159, 64)', 'rgb(153, 102, 255)']

function init() {
    main(statusURL)
}

function main(url) {
    let xhr = new XMLHttpRequest()
    // Get
    xhr.open('GET', url, true)
    xhr.responseType = 'json'
    xhr.onload = function() {
        if (this.status == 200) {
            report(this.response)
        } else {
            alert(this.status)
        }
    }
    xhr.onerror = function() {
        alert('No data')
    }
    // Send
    xhr.send()
}

function addCanvas(container, classSize) {
    let div = document.createElement('DIV')
    div.className = 'chart-container ' + classSize
    let canvas = document.createElement('CANVAS')
    div.appendChild(canvas)
    container.appendChild(div)
    return canvas
}

function status(sensors, container) {
    let table = document.createElement('TABLE')
    table.className = 'status'

    // Header
    let row = document.createElement('TR')
    let cell = document.createElement('TH')
    cell.textContent = 'Sensor'
    row.appendChild(cell)
    cell = document.createElement('TH')
    cell.textContent = 'Parameter'
    row.appendChild(cell)
    cell = document.createElement('TH')
    cell.textContent = 'Status'
    row.appendChild(cell)
    cell = document.createElement('TH')
    row.appendChild(cell)
    table.appendChild(row)

    // Sensor
    for (let sensor of Object.values(sensors)) {
        if ('alarms' in sensor) {
            for (let alarm of Object.values(sensor['alarms'])) {
                if (alarm['status'] != 'NA') {
                    row = document.createElement('TR')
                    cell = document.createElement('TD')
                    cell.textContent = sensor['label']
                    row.appendChild(cell)
                    cell = document.createElement('TD')
                    cell.textContent = alarm['parameter']
                    row.appendChild(cell)
                    cell = document.createElement('TD')
                    cell.textContent = alarm['status']
                    if (alarm['status'] == 'alert' || alarm['status'] == 'alarm') {
                        cell.className = 'alarm'
                    } else if (alarm['status'] == 'paused') {
                        cell.className = 'paused'
                    } else {
                        cell.className = 'ok'
                    }
                    row.appendChild(cell)
                    cell = document.createElement('TD')
                    let a = document.createElement('A')
                    a.className = 'action'
                    if (alarm['status'] == 'paused') {
                        a.href = `request/reset?mac=${sensor['mac']}&parameter=${alarm['parameter']}`
                        a.textContent = 'reset'
                    } else {
                        a.href = `request/pause?mac=${sensor['mac']}&parameter=${alarm['parameter']}`
                        a.textContent = 'pause'
                    }
                    cell.appendChild(a)
                    row.appendChild(cell)
                    table.appendChild(row)
                }
            }
        }
    }

    // Add table
    container.appendChild(table)
}

function epochToDate(t) {
    let date = new Date(t*1000)
    const month = date.toLocaleString('default', { month: 'short' })
    const minute = String(date.getMinutes())
    return `${month} ${date.getDate()} ${date.getHours()}:${minute.padStart(2, '0')}`
}

function plotScatter(ctx, data) {
    return new Chart(ctx, {
        type: 'scatter',
        data: {datasets: data},
        options: {
            maintainAspectRatio: false,
            scales: {
                x: {
                    type: 'linear',
                    ticks: {
                        callback: function(value, index, ticks) {
                            return epochToDate(value)
                        }
                    }
                },
                y: {
                    type: 'linear',
                    title: {
                        display:true,
                        text: 'Temperature (°C)',
                    },
                }
            },
            plugins: {
                title: {
                    text: 'Temperature',
                    display: true
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return `${context.parsed.y}°C @ ${epochToDate(context.parsed.x)}`
                        }
                    }
                }
            }
        }
    })
}

function report(rawData) {
    // Prepare data
    let temperatureDatasets = []
    let batteryData = []
    let batteryColor = []
    let batteryLabel = []
    let nsensor = 0
    let ncolor = colors.length
    for (let pair of Object.entries(rawData['data']['history'])) {
        let [mac, v] = pair
        if (v['temperature'].length > 0) {
            let sensorData = []
            for (let i=0, leni=v['temperature'].length; i<leni; i++) {
                sensorData.push({x:v['time'][i], y:v['temperature'][i]})
            }
            let ds = {
                label: rawData['sensors'][mac]['label'],
                data: sensorData,
                borderColor: colors[nsensor % ncolor],
                backgroundColor: colors[nsensor % ncolor],
                cubicInterpolationMode: 'monotone',
                showLine: true,
            }
            temperatureDatasets.push(ds)

            if ('battery' in v) {
                batteryData.push(v['battery'][v['battery'].length-1])
                batteryColor.push(colors[nsensor % ncolor])
                batteryLabel.push(rawData['sensors'][mac]['label'])
            }

            nsensor++
        }
    }
    nsensor = 0
    let temperatureSuppDatasets = []
    for (let pair of Object.entries(rawData['data']['supp_history'])) {
        let [mac, v] = pair
        if (v['temperature'].length > 0) {
            let sensorData = []
            for (let i=0, leni=v['temperature'].length; i<leni; i++) {
                sensorData.push({x:v['time'][i], y:v['temperature'][i]})
            }
            let ds = {
                label: rawData['sensors'][mac]['label'],
                data: sensorData,
                borderColor: colors[nsensor % ncolor],
                backgroundColor: colors[nsensor % ncolor],
                cubicInterpolationMode: 'monotone',
                showLine: true,
            }
            temperatureSuppDatasets.push(ds)

            nsensor++
        }
    }
    
    // Main
    let container = document.getElementById('main')

    // Status
    status(rawData['sensors'], container)

    // Temperature
    const temperatureChart = plotScatter(addCanvas(container, 'chart-large'), temperatureDatasets)
    const temperatureSuppChart = plotScatter(addCanvas(container, 'chart-large'), temperatureSuppDatasets)

    // Battery
    const ctxBatt = addCanvas(container, 'chart-small')
    const batteryChart = new Chart(ctxBatt, {
        type: 'bar',
        data: {
            labels: batteryLabel,
            datasets: [{
                data: batteryData,
                backgroundColor: batteryColor,
            }],
        },
        options: {
            maintainAspectRatio: false,
            scales: {
                y: {
                    type: 'linear',
                    title: {
                        display:true,
                        text: '%',
                    },
                }
            },
            plugins: {
                title: {
                    text: 'Battery',
                    display: true
                },
                legend: {
                    display: false,
                }
            }
        }
    })
}

addLoadEvent(init)
